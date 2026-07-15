"""Test della logica di write_carousel_prompts (parsing, retry su lunghezza
fuori target, assemblaggio col personaggio). run_headless e' mockato: la
qualita' reale delle descrizioni scritte da Claude e' stata verificata a
mano con foto vere durante lo sviluppo (vedi docs §12), non riproducibile
in modo affidabile in CI senza asset esterni e senza consumare la sessione
Claude ad ogni test."""

import json

import pytest

from aicraft.production import claude_creative
from aicraft.production.character import CharacterProfile
from aicraft.production.frame_picker import SampledFrame

_FRAME = SampledFrame(path="f0.jpg", timestamp_sec=0.0)
_FRAMES_2 = [SampledFrame(path="f0.jpg", timestamp_sec=0.0), SampledFrame(path="f1.jpg", timestamp_sec=4.0)]

_CHAR = CharacterProfile(
    creator_nome="Test",
    soul_id="soul-123",
    soul_name="TestSoul",
    physical_description="X" * 100,
    mandatory_additions="mandatory add",
    negative_prompt="no watermark",
)


def test_strip_markdown_fence_rimuove_blocco_json():
    fenced = '```json\n{"a": 1}\n```'
    assert claude_creative._strip_markdown_fence(fenced) == '{"a": 1}'


def test_strip_markdown_fence_rimuove_blocco_generico():
    fenced = '```\n{"a": 1}\n```'
    assert claude_creative._strip_markdown_fence(fenced) == '{"a": 1}'


def test_strip_markdown_fence_lascia_invariato_json_senza_fence():
    raw = '{"a": 1}'
    assert claude_creative._strip_markdown_fence(raw) == raw


def test_write_carousel_prompts_gestisce_risposta_con_fence_markdown(monkeypatch):
    scene_min, scene_max = claude_creative._scene_target_range(_CHAR)
    mid_len = (scene_min + scene_max) // 2
    scene = "scena " + "x" * (mid_len - 6)
    fenced_response = "```json\n" + json.dumps({"scenes": [scene]}) + "\n```"

    monkeypatch.setattr(claude_creative, "run_headless", lambda prompt, **kw: fenced_response)

    result = claude_creative.write_carousel_prompts(photo_paths=["a.jpg"], character=_CHAR, content_type="carosello")

    assert len(result) == 1
    assert scene in result[0]


def test_scene_target_range_lascia_spazio_al_fisso():
    scene_min, scene_max = claude_creative._scene_target_range(_CHAR)
    fixed_len = len(_CHAR.physical_description) + len(_CHAR.mandatory_additions) + len(_CHAR.negative_prompt)
    assert scene_min == claude_creative.TARGET_PROMPT_LEN_MIN - fixed_len - 6
    assert scene_max == claude_creative.TARGET_PROMPT_LEN_MAX - fixed_len - 6


def test_assemble_full_prompt_contiene_tutti_i_pezzi_fissi_verbatim():
    full = claude_creative._assemble_full_prompt(_CHAR, "scena di prova")
    assert _CHAR.physical_description in full
    assert _CHAR.mandatory_additions in full
    assert _CHAR.negative_prompt in full
    assert "scena di prova" in full


def test_write_carousel_prompts_rifiuta_lista_vuota():
    with pytest.raises(ValueError):
        claude_creative.write_carousel_prompts(photo_paths=[], character=_CHAR, content_type="carosello")


def test_write_carousel_prompts_successo_al_primo_tentativo(monkeypatch):
    scene_min, scene_max = claude_creative._scene_target_range(_CHAR)
    mid_len = (scene_min + scene_max) // 2
    scenes = [f"scena {i} " + "x" * (mid_len - 8) for i in range(3)]

    calls = {"n": 0}

    def fake_run_headless(prompt, **kwargs):
        calls["n"] += 1
        return json.dumps({"scenes": scenes})

    monkeypatch.setattr(claude_creative, "run_headless", fake_run_headless)

    result = claude_creative.write_carousel_prompts(
        photo_paths=["a.jpg", "b.jpg", "c.jpg"], character=_CHAR, content_type="carosello", source_category="GENERAL"
    )

    assert calls["n"] == 1  # nessun retry necessario
    assert len(result) == 3
    for full, scene in zip(result, scenes):
        assert scene in full
        assert _CHAR.physical_description in full


def test_write_carousel_prompts_retry_su_lunghezza_fuori_target(monkeypatch):
    scene_min, scene_max = claude_creative._scene_target_range(_CHAR)
    too_short = ["s"] * 3  # chiaramente sotto target
    good = [f"scena {i} " + "x" * ((scene_min + scene_max) // 2 - 8) for i in range(3)]

    responses = [json.dumps({"scenes": too_short}), json.dumps({"scenes": good})]
    calls = {"n": 0}

    def fake_run_headless(prompt, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        return responses[idx]

    monkeypatch.setattr(claude_creative, "run_headless", fake_run_headless)

    result = claude_creative.write_carousel_prompts(
        photo_paths=["a.jpg", "b.jpg", "c.jpg"], character=_CHAR, content_type="carosello"
    )

    assert calls["n"] == 2  # un retry
    assert all(len(s) for s in good)


def test_write_carousel_prompts_json_malformato_va_in_retry_poi_errore(monkeypatch):
    monkeypatch.setattr(claude_creative, "run_headless", lambda prompt, **kw: "non e' json")

    with pytest.raises(claude_creative.ClaudeCreativeError):
        claude_creative.write_carousel_prompts(photo_paths=["a.jpg"], character=_CHAR, content_type="carosello")


def test_write_carousel_prompts_numero_elementi_sbagliato_alla_fine_errore(monkeypatch):
    monkeypatch.setattr(claude_creative, "run_headless", lambda prompt, **kw: json.dumps({"scenes": ["solo una"]}))

    with pytest.raises(claude_creative.ClaudeCreativeError):
        claude_creative.write_carousel_prompts(
            photo_paths=["a.jpg", "b.jpg"], character=_CHAR, content_type="carosello"
        )


def test_write_carousel_prompts_esaurisce_retry_e_ritorna_comunque(monkeypatch, caplog):
    # sempre fuori target: dopo i retry deve arrendersi e ritornare l'ultimo risultato, non esplodere
    monkeypatch.setattr(claude_creative, "run_headless", lambda prompt, **kw: json.dumps({"scenes": ["corto", "corto2"]}))

    result = claude_creative.write_carousel_prompts(photo_paths=["a.jpg", "b.jpg"], character=_CHAR, content_type="carosello")

    assert len(result) == 2


# ---- write_talking_video_prompt ----

def test_write_talking_video_prompt_rifiuta_senza_frame():
    with pytest.raises(ValueError):
        claude_creative.write_talking_video_prompt(
            frames=[], transcript="ciao", character=_CHAR, content_type="video_talking",
            source_category="TALKING", duration_seconds=5.0, use_video_reference=False,
        )


def test_write_talking_video_prompt_rifiuta_senza_transcript():
    with pytest.raises(ValueError):
        claude_creative.write_talking_video_prompt(
            frames=[_FRAME], transcript="   ", character=_CHAR, content_type="video_talking",
            source_category="TALKING", duration_seconds=5.0, use_video_reference=False,
        )


def test_write_talking_video_prompt_assembla_col_personaggio(monkeypatch):
    monkeypatch.setattr(claude_creative, "run_headless", lambda prompt, **kw: "STYLE: selfie. DIALOGUE: 'ciao'.")

    result = claude_creative.write_talking_video_prompt(
        frames=_FRAMES_2, transcript="ciao a tutti", character=_CHAR,
        content_type="video_talking", source_category="TALKING", duration_seconds=8.0,
        use_video_reference=False,
    )

    assert _CHAR.physical_description in result
    assert _CHAR.mandatory_additions in result
    assert _CHAR.negative_prompt in result
    assert "STYLE: selfie." in result


def test_write_talking_video_prompt_gestisce_fence_markdown(monkeypatch):
    monkeypatch.setattr(claude_creative, "run_headless", lambda prompt, **kw: "```\nSTYLE: selfie.\n```")

    result = claude_creative.write_talking_video_prompt(
        frames=[_FRAME], transcript="ciao", character=_CHAR, content_type="video_talking",
        source_category="TALKING", duration_seconds=5.0, use_video_reference=False,
    )

    assert "```" not in result
    assert "STYLE: selfie." in result


def test_write_talking_video_prompt_risposta_vuota_solleva_errore(monkeypatch):
    monkeypatch.setattr(claude_creative, "run_headless", lambda prompt, **kw: "   ")

    with pytest.raises(claude_creative.ClaudeCreativeError):
        claude_creative.write_talking_video_prompt(
            frames=[_FRAME], transcript="ciao", character=_CHAR, content_type="video_talking",
            source_category="TALKING", duration_seconds=5.0, use_video_reference=False,
        )


def test_write_talking_video_prompt_menziona_uso_video_reference_solo_se_attivo(monkeypatch):
    seen_prompts = []

    def fake_run_headless(prompt, **kw):
        seen_prompts.append(prompt)
        return "scena finta"

    monkeypatch.setattr(claude_creative, "run_headless", fake_run_headless)

    claude_creative.write_talking_video_prompt(
        frames=[_FRAME], transcript="ciao", character=_CHAR, content_type="video_talking",
        source_category="TALKING", duration_seconds=5.0, use_video_reference=True,
    )
    claude_creative.write_talking_video_prompt(
        frames=[_FRAME], transcript="ciao", character=_CHAR, content_type="video_talking",
        source_category="TALKING", duration_seconds=5.0, use_video_reference=False,
    )

    assert "SOLO come riferimento di movimento" in seen_prompts[0]
    assert "Non c'e' nessun video di riferimento" in seen_prompts[1]


def test_write_talking_video_prompt_usa_timestamp_segmenti_quando_presenti(monkeypatch):
    seen_prompts = []

    def fake_run_headless(prompt, **kw):
        seen_prompts.append(prompt)
        return "scena finta"

    monkeypatch.setattr(claude_creative, "run_headless", fake_run_headless)

    segments = [{"start": 0.0, "end": 2.1, "text": "ciao a tutti"}, {"start": 2.1, "end": 4.5, "text": "oggi vi mostro"}]

    claude_creative.write_talking_video_prompt(
        frames=_FRAMES_2, transcript="ciao a tutti oggi vi mostro", transcript_segments=segments,
        character=_CHAR, content_type="video_talking", source_category="TALKING",
        duration_seconds=5.0, use_video_reference=False,
    )
    claude_creative.write_talking_video_prompt(
        frames=[_FRAME], transcript="ciao", transcript_segments=None,
        character=_CHAR, content_type="video_talking", source_category="TALKING",
        duration_seconds=5.0, use_video_reference=False,
    )

    assert "TIMESTAMP ESATTI" in seen_prompts[0]
    assert "0.0s" in seen_prompts[0] and "2.1s" in seen_prompts[0]
    assert "[0.0s]" in seen_prompts[0]  # timestamp del frame stesso, non solo dei segmenti
    assert "TIMESTAMP ESATTI" not in seen_prompts[1]  # senza segmenti: degrada al comportamento precedente


def test_adapt_original_caption_and_hashtags_parsa_json(monkeypatch):
    seen = {}

    def fake_run_headless(prompt, **kw):
        seen["prompt"] = prompt
        return json.dumps({"caption": "Nuova caption", "hashtags": ["#fit", "#ig"]})

    monkeypatch.setattr(claude_creative, "run_headless", fake_run_headless)

    result = claude_creative.adapt_original_caption_and_hashtags(
        original_caption="Caption originale #fit",
        transcript="ciao",
        content_type="video_talking",
    )

    assert result == {"caption": "Nuova caption", "hashtags": ["#fit", "#ig"]}
    assert "Caption originale #fit" in seen["prompt"]
    assert "non inventare" in seen["prompt"]
