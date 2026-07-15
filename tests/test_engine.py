"""Test del Production Engine con Higgsfield/Claude mockati (nessuna
credenziale necessaria): verificano l'orchestrazione degli stadi, il
CreditLedger e l'assemblaggio della cartella finale, non l'integrazione
reale con i servizi esterni (quella va verificata a parte, vedi
docs/ai-craft-architecture.md §7)."""

import datetime as dt
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft.db.base import Base
from aicraft.db.models import ContentPiece, ContentPieceEvent, Creator, CreditLedger, PlanWeek, Profile, ReferenceItem
from aicraft.production import engine as engine_module
from aicraft.production.higgsfield_client import GenerationResult


@pytest.fixture(autouse=True)
def _skip_if_no_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg non disponibile in questo ambiente")


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("aicraft.config.DELIVERY_DIR", tmp_path / "delivery")
    (tmp_path / "delivery").mkdir()

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    with TestSession() as s:
        yield s


def _make_video(path, duration=2):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True, check=True,
    )


def test_process_content_piece_video_talking_end_to_end(session, tmp_path, monkeypatch):
    creator = Creator(nome="Ruby")  # deve combaciare con character.CHARACTERS_BY_CREATOR
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_talking")
    source_video = tmp_path / "originale.mp4"
    _make_video(source_video)
    reference = ReferenceItem(
        source_url="https://www.instagram.com/reel/TEST/",
        status="ready",
        transcript="ciao a tutti oggi parliamo di skincare",
        source_category="TALKING",
        content_type_hint="video",
        local_video_path=str(source_video),
    )
    plan_week = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26))
    piece = ContentPiece(
        profile=profile, reference=reference, content_type="video_talking",
        plan_week=plan_week, scheduled_day="lun", status="reference_ready",
    )
    session.add_all([creator, profile, reference, plan_week, piece])
    session.commit()

    video_path = tmp_path / "generated.mp4"
    _make_video(video_path)

    monkeypatch.setattr(
        engine_module.frame_picker, "pick_reference_frame",
        lambda video_path, output_path, **kw: engine_module.frame_picker.FramePick(frame_path=tmp_path / "frame.jpg", timestamp_sec=0.0, method="fallback"),
    )
    monkeypatch.setattr(
        engine_module.claude_creative, "write_carousel_prompts",
        lambda **kwargs: ["un prompt di rigenerazione finto"],
    )
    monkeypatch.setattr(
        engine_module.frame_picker, "sample_frames",
        lambda video_path, output_dir, **kw: [tmp_path / "af0.jpg", tmp_path / "af1.jpg"],
    )
    monkeypatch.setattr(
        engine_module.claude_creative, "write_talking_video_prompt",
        lambda **kwargs: "un prompt video talking finto",
    )
    monkeypatch.setattr(
        engine_module.claude_creative, "write_caption_and_hashtags",
        lambda **kwargs: {"caption": "Caption finta", "hashtags": ["#test", "#skincare"]},
    )

    calls = {"image": 0, "video": 0}
    image_kwargs = {}
    video_kwargs = {}

    fake_image_path = tmp_path / "ruby2_image.png"
    fake_image_path.write_bytes(b"finta immagine")

    def fake_generate_image(prompt, **kwargs):
        calls["image"] += 1
        image_kwargs.update(kwargs)
        return GenerationResult(job_id="img-1", status="completed", result_url=str(fake_image_path), cost_credits=None, raw={})

    def fake_generate_video(prompt, **kwargs):
        calls["video"] += 1
        video_kwargs.update(kwargs)
        return GenerationResult(job_id="vid-1", status="completed", result_url=str(video_path), cost_credits=None, raw={})

    def fake_estimate_cost(job_type, **kwargs):
        return {"text2image_soul_v2": 2.0, "seedance_2_0": 5.0}[job_type]

    monkeypatch.setattr(engine_module.higgsfield_client, "generate_image", fake_generate_image)
    monkeypatch.setattr(engine_module.higgsfield_client, "generate_video", fake_generate_video)
    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", fake_estimate_cost)

    engine_module.process_content_piece(session, piece)

    assert piece.status == "delivered"
    assert calls == {"image": 1, "video": 1}
    assert image_kwargs.get("aspect_ratio") == "9:16"  # foto Ruby2 per video: verticale, deciso con l'utente
    # parametri seedance_2_0 decisi con l'utente (15/07/2026): 9:16 720p, audio acceso
    assert video_kwargs.get("aspect_ratio") == "9:16"
    assert video_kwargs.get("resolution") == "720p"
    assert video_kwargs.get("generate_audio") == "true"
    assert video_kwargs.get("video_references") is None  # toggle spento di default
    assert 1 <= video_kwargs.get("duration") <= 15  # durata REALE del video di test (~2s), non il worst-case=15
    assert piece.caption == "Caption finta"
    assert piece.hashtags == ["#test", "#skincare"]
    assert piece.cost_credits_actual == 7.0

    ledger_rows = session.query(CreditLedger).all()
    assert len(ledger_rows) == 2
    assert sorted(r.delta_credits for r in ledger_rows) == [-5.0, -2.0]

    delivered_folder = tmp_path / "delivery" / "ruby-wilde" / "video-talking" / f"2026-07-20_lun_{piece.id}"
    assert delivered_folder.exists()
    assert (delivered_folder / "caption.txt").read_text() == "Caption finta"
    assert (delivered_folder / "meta.json").exists()
    assert any(f.suffix == ".mp4" for f in delivered_folder.iterdir())

    events = session.query(ContentPieceEvent).filter_by(content_piece_id=piece.id).order_by(ContentPieceEvent.id).all()
    seen = [(e.stage, e.status) for e in events]
    assert seen == [
        ("image_regen", "started"), ("image_regen", "completed"),
        ("video_regen", "started"), ("video_regen", "completed"),
        ("qa", "started"), ("qa", "completed"),
        ("caption_hashtag", "started"), ("caption_hashtag", "completed"),
        ("delivery", "started"), ("delivery", "completed"),
        ("delivered", "completed"),
    ]
    completed_stage_events = [e for e in events if e.status == "completed" and e.stage != "delivered"]
    assert all(e.duration_seconds is not None and e.duration_seconds >= 0 for e in completed_stage_events)


def test_process_content_piece_qa_fallito_marca_errore(session, monkeypatch):
    creator = Creator(nome="Test Creator")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_talking")
    reference = ReferenceItem(source_url="https://www.instagram.com/reel/TEST2/", status="ready", transcript="test")
    piece = ContentPiece(profile=profile, reference=reference, content_type="video_talking", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    def fake_generate_image(prompt, **kwargs):
        return GenerationResult(job_id="img-1", status="completed", result_url="https://cdn.example/img.png", cost_credits=None, raw={})

    def fake_generate_video(prompt, **kwargs):
        return GenerationResult(job_id="vid-1", status="completed", result_url="/percorso/che/non/esiste.mp4", cost_credits=None, raw={})

    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", lambda job_type, **kwargs: None)
    monkeypatch.setattr(engine_module.higgsfield_client, "generate_image", fake_generate_image)
    monkeypatch.setattr(engine_module.higgsfield_client, "generate_video", fake_generate_video)

    engine_module.process_content_piece(session, piece)

    assert piece.status == "error"


def test_process_content_piece_claude_rifiuta_marca_content_refused(session, tmp_path, monkeypatch):
    """Quando Claude rifiuta di scrivere il prompt (policy di contenuto), il
    pezzo va marcato con uno stato dedicato invece di "error" generico —
    esito legittimo e non recuperabile con un retry, stesso principio di
    blocked_nsfw/too_long. Vedi docs/ai-craft-architecture.md §16."""
    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    frame_paths = [str(tmp_path / "foto_0.jpg")]
    reference = ReferenceItem(
        source_url="https://www.instagram.com/p/REFUSED/",
        status="ready", frame_paths=frame_paths, source_category="GENERAL",
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="carosello", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    def fake_write_carousel_prompts(**kwargs):
        raise engine_module.claude_creative.ClaudeContentRefusedError("rifiuto simulato")

    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", fake_write_carousel_prompts)

    engine_module.process_content_piece(session, piece)

    assert piece.status == "content_refused"
    assert piece.was_refused is True


def test_retry_content_piece_azzera_asset_e_passa_avoid_refusal(session, tmp_path, monkeypatch):
    """retry_content_piece riparte da image_regen (asset/caption azzerati) e
    passa avoid_refusal=True quando il pezzo era stato rifiutato in
    precedenza, cosi' Claude scrive un prompt piu' prudente invece di
    ripetere lo stesso input che darebbe lo stesso rifiuto."""
    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    frame_paths = [str(tmp_path / "foto_0.jpg")]
    reference = ReferenceItem(
        source_url="https://www.instagram.com/p/RETRY_PIECE/",
        status="ready", frame_paths=frame_paths, source_category="GENERAL",
    )
    piece = ContentPiece(
        profile=profile, reference=reference, content_type="carosello", status="content_refused",
        was_refused=True, generated_assets=["/tmp/vecchia_immagine.jpg"], caption="vecchia caption",
    )
    session.add_all([creator, profile, reference, piece])
    session.commit()

    seen = {}

    def fake_write_carousel_prompts(**kwargs):
        seen["avoid_refusal"] = kwargs.get("avoid_refusal")
        raise engine_module.claude_creative.ClaudeContentRefusedError("rifiuto simulato di nuovo")

    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", fake_write_carousel_prompts)

    result = engine_module.retry_content_piece(session, piece.id)

    assert seen["avoid_refusal"] is True
    assert result["status"] == "content_refused"


def test_retry_content_piece_rifiuta_pezzo_gia_consegnato(session):
    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    piece = ContentPiece(profile=profile, content_type="carosello", status="delivered")
    session.add_all([creator, profile, piece])
    session.commit()

    with pytest.raises(ValueError):
        engine_module.retry_content_piece(session, piece.id)


def test_process_content_piece_tipo_sconosciuto_marca_errore(session):
    creator = Creator(nome="Test Creator")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_talking")
    piece = ContentPiece(profile=profile, content_type="tipo_inesistente", status="reference_ready")
    session.add_all([creator, profile, piece])
    session.commit()

    engine_module.process_content_piece(session, piece)

    assert piece.status == "error"


def test_caption_hashtag_adatta_caption_originale_quando_presente(monkeypatch):
    reference = ReferenceItem(
        source_url="https://www.instagram.com/reel/CAPTION/",
        status="ready",
        transcript="ciao a tutti",
        original_caption="Caption originale #old",
    )
    piece = ContentPiece(content_type="video_talking", status="caption_hashtag")

    seen = {}

    def fake_adapt_original_caption_and_hashtags(**kwargs):
        seen.update(kwargs)
        return {"caption": "Caption adattata", "hashtags": ["#old", "#new"]}

    monkeypatch.setattr(engine_module.claude_creative, "adapt_original_caption_and_hashtags", fake_adapt_original_caption_and_hashtags)
    monkeypatch.setattr(
        engine_module.claude_creative,
        "write_caption_and_hashtags",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("non deve inventare da zero")),
    )

    engine_module._stage_caption_hashtag(piece, reference)

    assert seen["original_caption"] == "Caption originale #old"
    assert seen["transcript"] == "ciao a tutti"
    assert piece.caption == "Caption adattata"
    assert piece.hashtags == ["#old", "#new"]


def test_video_balletti_usa_motion_control_con_video_originale(session, tmp_path, monkeypatch):
    """video_balletti ha una convenzione di chiamata diversa dagli altri
    video (niente prompt, serve il video originale) — vedi pipeline_spec.py
    e docs §12.2."""
    creator = Creator(nome="Ruby")  # deve combaciare con character.CHARACTERS_BY_CREATOR
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_balletti")
    original_video = tmp_path / "originale.mp4"
    _make_video(original_video)
    reference = ReferenceItem(
        source_url="https://www.instagram.com/reel/BALLETTO/",
        status="ready",
        local_video_path=str(original_video),
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="video_balletti", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    monkeypatch.setattr(
        engine_module.frame_picker, "pick_reference_frame",
        lambda video_path, output_path, **kw: engine_module.frame_picker.FramePick(frame_path=tmp_path / "frame.jpg", timestamp_sec=0.0, method="fallback"),
    )
    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", lambda **kwargs: ["prompt"])
    monkeypatch.setattr(
        engine_module.claude_creative, "write_caption_and_hashtags",
        lambda **kwargs: {"caption": "c", "hashtags": []},
    )
    fake_ruby2_image = tmp_path / "ruby2.png"
    fake_ruby2_image.write_bytes(b"finta immagine")
    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", lambda job_type, **kw: 0.12)
    monkeypatch.setattr(
        engine_module.higgsfield_client, "generate_image",
        lambda prompt, **kw: GenerationResult(job_id="img-1", status="completed", result_url=str(fake_ruby2_image), cost_credits=None, raw={}),
    )

    calls = {}

    def fake_motion_control(image_reference, video_reference, **kw):
        calls["image_reference"] = image_reference
        calls["video_reference"] = video_reference
        return GenerationResult(job_id="mc-1", status="completed", result_url=str(original_video), cost_credits=None, raw={})

    monkeypatch.setattr(engine_module.higgsfield_client, "generate_motion_control", fake_motion_control)

    engine_module.process_content_piece(session, piece)

    assert piece.status == "delivered"
    assert calls["video_reference"] == str(original_video)  # il video ORIGINALE, non quello generato
    assert calls["image_reference"] == str(fake_ruby2_image)  # la foto Ruby2 appena generata (locale, non URL remoto)
    # 0.12 (image_regen, mockato) + 18.0 (video_regen, manual_cost_estimate)
    assert piece.cost_credits_actual == pytest.approx(18.12)


def test_video_balletti_bloccato_nsfw_marca_stato_dedicato(session, tmp_path, monkeypatch):
    creator = Creator(nome="Ruby")  # deve combaciare con character.CHARACTERS_BY_CREATOR
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_balletti")
    reference = ReferenceItem(
        source_url="https://www.instagram.com/reel/BALLETTO2/",
        status="ready",
        local_video_path="/finto/video.mp4",
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="video_balletti", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    monkeypatch.setattr(engine_module.qa, "get_duration_seconds", lambda path: 10.0)  # entro soglia, path finto va bene
    monkeypatch.setattr(
        engine_module.frame_picker, "pick_reference_frame",
        lambda video_path, output_path, **kw: engine_module.frame_picker.FramePick(frame_path=tmp_path / "frame.jpg", timestamp_sec=0.0, method="fallback"),
    )
    fake_ruby2_image = tmp_path / "ruby2.png"
    fake_ruby2_image.write_bytes(b"finta immagine")
    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", lambda **kwargs: ["prompt"])
    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", lambda job_type, **kw: 0.12)
    monkeypatch.setattr(
        engine_module.higgsfield_client, "generate_image",
        lambda prompt, **kw: GenerationResult(job_id="img-1", status="completed", result_url=str(fake_ruby2_image), cost_credits=None, raw={}),
    )

    def fake_motion_control(image_reference, video_reference, **kw):
        raise engine_module.higgsfield_client.HiggsfieldNSFWBlockedError('job x ended with status "nsfw"')

    monkeypatch.setattr(engine_module.higgsfield_client, "generate_motion_control", fake_motion_control)

    engine_module.process_content_piece(session, piece)

    assert piece.status == "blocked_nsfw"  # non "error" generico: esito legittimo, non recuperabile con retry


def test_video_troppo_lungo_scartato_senza_spendere_nulla(session, tmp_path, monkeypatch):
    """Video originale >15s: scartato PRIMA di qualunque chiamata
    Claude/Higgsfield, non solo marcato dopo — deciso con l'utente."""
    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_talking")
    long_video = tmp_path / "lungo.mp4"
    _make_video(long_video, duration=20)  # supera i 15s
    reference = ReferenceItem(
        source_url="https://www.instagram.com/reel/LUNGO/",
        status="ready",
        local_video_path=str(long_video),
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="video_talking", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    calls = {"n": 0}
    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", lambda **kw: calls.__setitem__("n", calls["n"] + 1) or ["prompt"])
    monkeypatch.setattr(engine_module.higgsfield_client, "generate_image", lambda prompt, **kw: (_ for _ in ()).throw(AssertionError("non doveva essere chiamato")))

    engine_module.process_content_piece(session, piece)

    assert piece.status == "too_long"
    assert calls["n"] == 0  # niente chiamata Claude: scartato prima, non spreca nulla

    events = session.query(ContentPieceEvent).filter_by(content_piece_id=piece.id).order_by(ContentPieceEvent.id).all()
    seen = [(e.stage, e.status) for e in events]
    assert seen == [("image_regen", "started"), ("image_regen", "failed")]
    failed_event = events[-1]
    assert failed_event.duration_seconds is not None and failed_event.duration_seconds >= 0
    assert "supera il limite" in failed_event.detail


def test_video_entro_soglia_procede_normalmente(session, tmp_path, monkeypatch):
    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_talking")
    short_video = tmp_path / "corto.mp4"
    _make_video(short_video, duration=10)  # entro i 15s
    reference = ReferenceItem(
        source_url="https://www.instagram.com/reel/CORTO/",
        status="ready",
        local_video_path=str(short_video),
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="video_talking", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", lambda **kw: ["prompt"])
    monkeypatch.setattr(
        engine_module.frame_picker, "sample_frames",
        lambda video_path, output_dir, **kw: [tmp_path / "af0.jpg"],
    )
    monkeypatch.setattr(engine_module.claude_creative, "write_talking_video_prompt", lambda **kw: "prompt")
    monkeypatch.setattr(engine_module.claude_creative, "write_caption_and_hashtags", lambda **kw: {"caption": "c", "hashtags": []})
    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", lambda job_type, **kw: 0.12)
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"finta immagine")
    monkeypatch.setattr(
        engine_module.higgsfield_client, "generate_image",
        lambda prompt, **kw: GenerationResult(job_id="img-1", status="completed", result_url=str(fake_image_path), cost_credits=None, raw={}),
    )
    monkeypatch.setattr(
        engine_module.higgsfield_client, "generate_video",
        lambda prompt, **kw: GenerationResult(job_id="vid-1", status="completed", result_url=str(short_video), cost_credits=None, raw={}),
    )

    engine_module.process_content_piece(session, piece)

    assert piece.status == "delivered"  # non scartato: 10s < 15s


def test_video_talking_usa_video_reference_se_flag_attivo(session, tmp_path, monkeypatch):
    """Il toggle settings.SEEDANCE_USE_VIDEO_REFERENCE (default OFF, deciso
    con l'utente 15/07/2026) attiva il passaggio del video originale come
    video_references a seedance_2_0 — SOLO per movimento, l'identita' resta
    vincolata a start_image/personaggio (write_talking_video_prompt riceve
    use_video_reference per scriverlo esplicitamente nel prompt)."""
    from aicraft.production import settings as settings_module

    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="solo_talking")
    short_video = tmp_path / "corto.mp4"
    _make_video(short_video, duration=5)
    reference = ReferenceItem(
        source_url="https://www.instagram.com/reel/CORTO2/",
        status="ready",
        transcript="ciao a tutti",
        local_video_path=str(short_video),
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="video_talking", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    settings_module.set_flag(session, settings_module.SEEDANCE_USE_VIDEO_REFERENCE, True)

    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", lambda **kw: ["prompt"])
    monkeypatch.setattr(
        engine_module.frame_picker, "sample_frames",
        lambda video_path, output_dir, **kw: [tmp_path / "af0.jpg"],
    )
    seen_use_video_reference = {}

    def fake_write_talking_video_prompt(**kw):
        seen_use_video_reference["value"] = kw["use_video_reference"]
        return "prompt"

    monkeypatch.setattr(engine_module.claude_creative, "write_talking_video_prompt", fake_write_talking_video_prompt)
    monkeypatch.setattr(engine_module.claude_creative, "write_caption_and_hashtags", lambda **kw: {"caption": "c", "hashtags": []})
    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", lambda job_type, **kw: 0.12)
    fake_image_path = tmp_path / "img.png"
    fake_image_path.write_bytes(b"finta immagine")
    monkeypatch.setattr(
        engine_module.higgsfield_client, "generate_image",
        lambda prompt, **kw: GenerationResult(job_id="img-1", status="completed", result_url=str(fake_image_path), cost_credits=None, raw={}),
    )

    video_kwargs = {}

    def fake_generate_video(prompt, **kw):
        video_kwargs.update(kw)
        return GenerationResult(job_id="vid-1", status="completed", result_url=str(short_video), cost_credits=None, raw={})

    monkeypatch.setattr(engine_module.higgsfield_client, "generate_video", fake_generate_video)

    engine_module.process_content_piece(session, piece)

    assert piece.status == "delivered"
    assert seen_use_video_reference["value"] is True
    assert video_kwargs.get("video_references") == [str(short_video)]  # movimento
    assert video_kwargs.get("start_image") == str(fake_image_path)  # identita': sempre dalla foto Ruby2 (locale)


def test_carosello_usa_carousel_selection_e_genera_una_foto_per_prompt(session, tmp_path, monkeypatch):
    """content_type='carosello': le foto vengono da reference.frame_paths
    (gia' scaricate) via carousel_selection.py, non da un frame video. Ogni
    prompt tornato da write_carousel_prompts genera un'immagine distinta con
    il custom_reference_id del personaggio (Ruby2)."""
    creator = Creator(nome="Ruby")  # deve combaciare con character.CHARACTERS_BY_CREATOR
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    frame_paths = [str(tmp_path / f"foto_{i}.jpg") for i in range(3)]
    reference = ReferenceItem(
        source_url="https://www.instagram.com/p/CAROSELLO/",
        status="ready",
        frame_paths=frame_paths,
        source_category="GENERAL",
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="carosello", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    seen_photo_paths = {}

    def fake_write_carousel_prompts(*, photo_paths, character, content_type, source_category, avoid_refusal=False):
        seen_photo_paths["value"] = photo_paths
        return [f"prompt per {p}" for p in photo_paths]

    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", fake_write_carousel_prompts)
    monkeypatch.setattr(
        engine_module.claude_creative, "write_caption_and_hashtags",
        lambda **kwargs: {"caption": "c", "hashtags": []},
    )
    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", lambda job_type, **kw: 0.12)

    image_calls = []

    def fake_generate_image(prompt, **kw):
        image_calls.append((prompt, kw.get("custom_reference_id"), kw.get("aspect_ratio")))
        fake_path = tmp_path / f"generated_{len(image_calls)}.png"
        fake_path.write_bytes(b"finta immagine")
        return GenerationResult(job_id=f"img-{len(image_calls)}", status="completed", result_url=str(fake_path), cost_credits=None, raw={})

    monkeypatch.setattr(engine_module.higgsfield_client, "generate_image", fake_generate_image)

    # carosello: niente video_regen in PIPELINE_STAGES, e il file non deve
    # necessariamente esistere fisicamente perche' generate_image e'
    # mockato (nessun QA su file reale in questo test).
    monkeypatch.setattr(engine_module.qa, "check_image", lambda path: engine_module.qa.QAResult(passed=True, checks={}))

    engine_module.process_content_piece(session, piece)

    assert piece.status == "delivered"
    assert seen_photo_paths["value"] == frame_paths  # selezione carousel_selection passata cosi' com'e' (3 foto, <=3)
    assert len(image_calls) == 3  # una generazione per prompt
    assert all(ref == "0698f81f-1d26-47bb-b31b-9391aeadb144" for _, ref, _ in image_calls)  # soul_id di Ruby2
    assert all(ar == "1:1" for _, _, ar in image_calls)  # carosello: quadrato, deciso con l'utente
    assert piece.cost_credits_actual == pytest.approx(0.36)  # 0.12 x 3


def test_stage_image_regen_scarica_asset_remoto_in_locale(session, tmp_path, monkeypatch):
    """_localize_asset deve scaricare un vero URL remoto restituito da
    Higgsfield invece di lasciarlo com'e' in generated_assets — altrimenti
    QA/delivery non funzionano mai su un asset reale (gap trovato in review,
    15/07/2026, vedi docs §16). Qui si mocka solo requests.get, non
    _localize_asset: verifica l'integrazione vera."""
    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    frame_paths = [str(tmp_path / "foto_0.jpg")]
    reference = ReferenceItem(
        source_url="https://www.instagram.com/p/DOWNLOADTEST/",
        status="ready", frame_paths=frame_paths, source_category="GENERAL",
    )
    piece = ContentPiece(profile=profile, reference=reference, content_type="carosello", status="reference_ready")
    session.add_all([creator, profile, reference, piece])
    session.commit()

    monkeypatch.setattr(engine_module.config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(engine_module.claude_creative, "write_carousel_prompts", lambda **kw: ["prompt"])
    monkeypatch.setattr(engine_module.higgsfield_client, "estimate_cost", lambda job_type, **kw: 0.12)
    monkeypatch.setattr(
        engine_module.higgsfield_client, "generate_image",
        lambda prompt, **kw: GenerationResult(job_id="img-1", status="completed", result_url="https://cdn.example/real.png", cost_credits=None, raw={}),
    )

    class FakeResponse:
        content = b"bytes immagine finta"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(engine_module.higgsfield_client.requests, "get", lambda url, timeout=None: FakeResponse())

    engine_module._stage_image_regen(session, piece, reference, profile)

    assert len(piece.generated_assets) == 1
    saved_path = Path(piece.generated_assets[0])
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"bytes immagine finta"
    assert str(saved_path).startswith(str(tmp_path / "work"))


def test_run_once_produce_solo_piani_approvati(session, monkeypatch):
    creator = Creator(nome="Test Creator")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    plan_bozza = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26), status="bozza")
    plan_appr = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 27), week_end=dt.date(2026, 8, 2), status="approvato")
    ref = ReferenceItem(
        source_url="https://www.instagram.com/p/READY/",
        source_tab="CAROSELLI",
        source_category="BOOBS",
        content_type_hint="carosello",
        week_start=dt.date(2026, 7, 20),
        week_end=dt.date(2026, 7, 26),
        sheet_order=1,
        status="ready",
        frame_paths=["/tmp/foto.jpg"],
    )
    piece_bozza = ContentPiece(profile=profile, content_type="carosello", plan_week=plan_bozza, status="reference_ready")
    piece_appr = ContentPiece(profile=profile, content_type="carosello", plan_week=plan_appr, status="reference_ready")
    piece_senza_piano = ContentPiece(profile=profile, content_type="carosello", status="reference_ready")
    session.add_all([creator, profile, plan_bozza, plan_appr, ref, piece_bozza, piece_appr, piece_senza_piano])
    session.commit()

    processed = []
    monkeypatch.setattr(engine_module, "process_content_piece", lambda s, p: processed.append(p.id))

    engine_module.run_once(session)

    # solo il pezzo del piano approvato
    assert processed == [piece_appr.id]


def test_run_once_produce_prima_i_pezzi_con_priorita_piu_alta(session, monkeypatch):
    creator = Creator(nome="Test Creator")
    profile = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26), status="approvato")
    ref = ReferenceItem(
        source_url="https://www.instagram.com/p/PRIO/", status="ready", frame_paths=["/tmp/foto.jpg"],
    )
    piece_normale = ContentPiece(profile=profile, content_type="carosello", plan_week=plan, status="reference_ready", reference=ref, priority=0)
    piece_urgente = ContentPiece(profile=profile, content_type="carosello", plan_week=plan, status="reference_ready", priority=5)
    session.add_all([creator, profile, plan, ref, piece_normale, piece_urgente])
    session.commit()
    # piece_urgente non ha reference_id valorizzato tramite relazione ref
    # separata: qui basta che compaia PRIMA nell'ordine, non serve produrlo
    # per davvero (process_content_piece e' mockato sotto).
    piece_urgente.reference_id = ref.id
    session.commit()

    processed = []
    monkeypatch.setattr(engine_module, "process_content_piece", lambda s, p: processed.append(p.id))

    engine_module.run_once(session)

    assert processed[0] == piece_urgente.id
    assert processed[1] == piece_normale.id
