"""Test del bridge API dell'app desktop, senza GUI e senza rete (stima costi
finta). Verifica che i metodi chiamabili dal frontend ritornino i dati reali
attesi e gestiscano gli errori come {ok: False}."""

import datetime as dt
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft.budget import ledger
from aicraft.db.base import Base
from aicraft.db.models import CharacterVersion, ContentPiece, ContentPieceEvent, PlanWeek, Profile, ReferenceItem
from aicraft.desktop import api as api_mod
from aicraft.production import higgsfield_client
from aicraft.reference_sync import sync as reference_sync


def _has_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _make_video(path, duration=2):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=5", str(path)],
        capture_output=True, check=True,
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'desktop.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(api_mod, "SessionLocal", TestSession)
    # nessuna rete: default_cost_fn passa da higgsfield_client.estimate_cost
    monkeypatch.setattr(
        higgsfield_client, "estimate_cost",
        lambda job_type, **kw: {"text2image_soul_v2": 0.12, "seedance_2_0": 10.0}[job_type],
    )
    # backup.run_backup_safe legge config.DATABASE_URL direttamente (non la
    # sessione di test sopra): senza questo mock, production_run finirebbe
    # per copiare il DB REALE del progetto in data/backups/ ad ogni test.
    monkeypatch.setattr(api_mod.backup, "run_backup_safe", lambda: {"ok": True, "path": "finto"})
    return api_mod.Api()


def test_meta(api):
    r = api.meta()
    assert r["ok"]
    assert "video_talking" in r["content_types"]
    assert r["giorni"][0] == "lun"


def test_overview_vuoto(api):
    r = api.overview()
    assert r["ok"]
    assert r["overview"]["saldo_crediti"] == 0.0
    assert r["overview"]["profili"] == []


def test_today_agenda_senza_profilo_attivo(api):
    r = api.today_agenda()
    assert r["ok"]
    assert r["has_profile"] is False
    assert r["pieces"] == []


def test_today_agenda_senza_piano_per_la_settimana_corrente(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    api.set_active_profile(1)

    r = api.today_agenda()
    assert r["ok"]
    assert r["has_profile"] is True
    assert r["plan"] is None
    assert r["pieces"] == []


def test_today_agenda_con_piano_e_pezzo_pianificato_oggi(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    api.set_active_profile(1)

    today = dt.date.today()
    week_start = today - dt.timedelta(days=today.weekday())
    week_end = week_start + dt.timedelta(days=6)
    giorni = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
    oggi_giorno = giorni[today.weekday()]

    plan = api.create_plan(1, week_start.isoformat(), week_end.isoformat())
    plan_id = plan["plan"]["id"]
    api.plan_set_cell(plan_id, "carosello", oggi_giorno, 1)

    r = api.today_agenda()
    assert r["ok"]
    assert r["giorno"] == oggi_giorno
    assert r["plan"]["id"] == plan_id
    assert len(r["pieces"]) == 1
    assert r["pieces"][0]["content_type"] == "carosello"
    assert r["pieces"][0]["has_reference"] is False


def test_creator_e_profilo_flow(api):
    assert api.create_creator("Trinity")["ok"]
    r = api.create_profile(1, "Ruby Wilde", "misto")
    assert r["ok"]
    lp = api.list_profiles()
    assert lp["ok"] and len(lp["profiles"]) == 1
    assert lp["profiles"][0]["nome"] == "Ruby Wilde"

    act = api.set_active_profile(1)
    assert act["ok"]
    lp2 = api.list_profiles()
    assert lp2["profiles"][0]["is_active"] is True


def test_create_profile_tipo_invalido_ritorna_errore(api):
    api.create_creator("Trinity")
    r = api.create_profile(1, "X", "inventato")
    assert r["ok"] is False
    assert "tipo_contenuto" in r["error"]


def test_delete_profile_endpoint(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    api.create_profile(1, "Nova", "solo_talking")

    r = api.delete_profile(1)
    assert r["ok"]
    lp = api.list_profiles()
    assert [p["nome"] for p in lp["profiles"]] == ["Nova"]


def test_delete_profile_con_dipendenze_ritorna_errore_gestito(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    api.create_plan(1, "2026-07-20", "2026-07-26")

    r = api.delete_profile(1)
    assert r["ok"] is False
    assert "piani" in r["error"]


def test_budget_topup_e_status(api):
    assert api.budget_topup(100.0)["ok"]
    r = api.budget_status()
    assert r["ok"] and r["balance"] == 100.0


def test_piano_grid_e_stepper(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    pl = api.create_plan(1, "2026-07-20", "2026-07-26")
    assert pl["ok"]
    plan_id = pl["plan"]["id"]

    r = api.plan_set_cell(plan_id, "video_balletti", "mar", 2)
    assert r["ok"]
    assert r["plan"]["grid"]["video_balletti"]["mar"] == 2
    assert r["plan"]["totals_by_type"]["video_balletti"] == 2
    assert r["plan"]["totals_by_day"]["mar"] == 2
    assert r["plan"]["total"] == 2

    # decremento
    r2 = api.plan_set_cell(plan_id, "video_balletti", "mar", 1)
    assert r2["plan"]["grid"]["video_balletti"]["mar"] == 1


def test_approvazione_bloccata_e_poi_ok(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    plan_id = api.create_plan(1, "2026-07-20", "2026-07-26")["plan"]["id"]
    api.plan_set_cell(plan_id, "video_talking", "lun", 1)  # costo 10.12

    # saldo 0 -> bloccato
    blocked = api.approve_plan(plan_id)
    assert blocked["ok"] is False
    assert blocked["kind"] == "budget"

    # ricarico e riprovo
    api.budget_topup(100.0)
    ok = api.approve_plan(plan_id)
    assert ok["ok"]
    assert ok["plan"]["status"] == "approvato"
    assert ok["reference_assignment"]["missing"] == 1


def test_approvazione_assegna_reference_pronta(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    plan_id = api.create_plan(1, "2026-07-20", "2026-07-26")["plan"]["id"]
    api.plan_set_cell(plan_id, "carosello", "lun", 1)
    with api_mod.SessionLocal() as session:
        session.add(ReferenceItem(
            source_url="https://www.instagram.com/p/OK/",
            source_tab="CAROSELLI",
            source_category="BOOBS",
            content_type_hint="carosello",
            week_start=dt.date(2026, 7, 13),
            week_end=dt.date(2026, 7, 19),
            sheet_order=1,
            status="ready",
            frame_paths=["/tmp/foto.jpg"],
        ))
        session.commit()
    api.budget_topup(100.0)

    ok = api.approve_plan(plan_id)

    assert ok["ok"]
    assert ok["reference_assignment"]["assigned"] == 1
    assert ok["plan"]["missing_references"] == 0


def test_budget_status_con_piano_mostra_copertura(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    plan_id = api.create_plan(1, "2026-07-20", "2026-07-26")["plan"]["id"]
    api.plan_set_cell(plan_id, "carosello", "lun", 1)  # costo 0.36 (count=3, stima conservativa)
    api.budget_topup(50.0)

    r = api.budget_status(plan_id)
    assert r["ok"]
    assert r["plan_cost"] == pytest.approx(0.36)
    assert r["covers"] is True
    assert r["coverage"] == pytest.approx(49.64)


def test_production_preview_solo_piani_approvati(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    plan_id = api.create_plan(1, "2026-07-20", "2026-07-26")["plan"]["id"]
    api.plan_set_cell(plan_id, "carosello", "lun", 2)

    # piano in bozza: niente in coda
    assert api.production_preview()["ready_count"] == 0

    api.budget_topup(100.0)
    api.approve_plan(plan_id)
    api.assign_plan_references(plan_id)
    prev = api.production_preview()
    assert prev["ready_count"] == 0  # nessuna reference pronta nel DB locale


def test_production_run_richiede_conferma(api):
    r = api.production_run(confirmation=None)

    assert r["ok"] is False
    assert "Conferma richiesta" in r["error"]


def test_production_run_chiama_engine_se_pronto(api, monkeypatch):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    api.budget_topup(100.0)
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26), status="approvato")
        ref = ReferenceItem(
            source_url="https://www.instagram.com/p/RUN/",
            source_tab="CAROSELLI",
            source_category="BOOBS",
            content_type_hint="carosello",
            week_start=dt.date(2026, 7, 13),
            week_end=dt.date(2026, 7, 19),
            sheet_order=1,
            status="ready",
            frame_paths=["/tmp/foto.jpg"],
        )
        piece = ContentPiece(profile=profile, plan_week=plan, reference=ref, content_type="carosello", status="reference_ready")
        session.add_all([plan, ref, piece])
        session.commit()

    seen = {}

    def fake_run_once(session, plan_id=None):
        seen["plan_id"] = plan_id
        return {"approved_plans": 1, "assigned_references": 0, "missing_references": 0, "processed": 1, "delivered": 1, "failed": 0}

    monkeypatch.setattr(api_mod.production_engine, "run_once", fake_run_once)

    r = api.production_run(confirmation="PRODUCI")

    assert r["ok"]
    assert seen["plan_id"] is None
    assert r["preview_before"]["ready_count"] == 1
    assert r["production"]["delivered"] == 1


def test_reference_stats_mostra_settimane_categorie_e_latest(api):
    with api_mod.SessionLocal() as session:
        session.add(ReferenceItem(
            source_url="https://www.instagram.com/p/A/",
            source_tab="CAROSELLI",
            source_category="BOOBS",
            content_type_hint="carosello",
            week_start=dt.date(2026, 7, 13),
            week_end=dt.date(2026, 7, 19),
            sheet_order=1,
            status="ready",
            frame_paths=["/tmp/a.jpg"],
            original_caption="ciao",
            downloaded_at=dt.datetime(2026, 7, 15, 12, 0),
        ))
        vecchia = dt.datetime(2020, 1, 1)
        session.add(_seed_reference(status="download_error", category="BOOTY", download_attempts=1, downloaded_at=vecchia))
        session.add(_seed_reference(status="unavailable", category="GENERAL", download_attempts=api_mod.reference_sync.MAX_DOWNLOAD_ATTEMPTS, downloaded_at=vecchia))
        session.commit()

    r = api.reference_stats()

    assert r["ok"]
    assert r["ready"] == 1
    assert r["by_week"]["2026-07-13"] == 1
    assert r["by_category"]["CAROSELLI / BOOBS"] == 1
    assert r["latest"][0]["has_caption"] is True
    assert r["error"] == 2  # entrambe contano come errore
    assert r["error_retryable"] == 1  # solo quella che non ha esaurito i tentativi


def test_references_sync_endpoint(api, monkeypatch):
    def fake_run_once(max_items=None, source_tab=None, source_category=None):
        with api_mod.SessionLocal() as session:
            session.add(ReferenceItem(
                source_url="https://www.instagram.com/reel/T/",
                source_tab="VIRAL GENERAL",
                source_category="TALKING",
                content_type_hint="video",
                week_start=dt.date(2026, 7, 13),
                week_end=dt.date(2026, 7, 19),
                sheet_order=1,
                status="ready",
                local_video_path="/tmp/t.mp4",
            ))
            session.commit()

    monkeypatch.setattr(reference_sync, "run_once", fake_run_once)

    r = api.references_sync()

    assert r["ok"]
    assert r["total"] == 1
    assert r["by_category"]["VIRAL GENERAL / TALKING"] == 1


def test_references_sync_policy_endpoint(api, monkeypatch):
    def fake_run_policy_once(policy=None):
        with api_mod.SessionLocal() as session:
            session.add(ReferenceItem(
                source_url="https://www.instagram.com/p/POLICY/",
                source_tab="CAROSELLI",
                source_category="BOOTY",
                content_type_hint="carosello",
                week_start=dt.date(2026, 7, 13),
                week_end=dt.date(2026, 7, 19),
                sheet_order=1,
                status="download_error",
                frame_paths=[],
            ))
            session.commit()
        return {"sheet_refs": 1, "processed": 1, "cleanup_deleted": 0, "policy": []}

    monkeypatch.setattr(reference_sync, "run_policy_once", fake_run_policy_once)

    r = api.references_sync_policy(policy="CAROSELLI:BOOTY=1")

    assert r["ok"]
    assert r["sync"]["processed"] == 1
    assert r["error"] == 1
    assert r["by_status"]["download_error"] == 1


def test_backlog_add_e_list(api):
    r = api.add_backlog_note("qualita", "Migliorare fedelta posa/outfit", "vedi carosello reale del 15/07")
    assert r["ok"]

    lp = api.list_backlog()
    assert lp["ok"]
    assert len(lp["notes"]) == 1
    assert lp["notes"][0]["title"] == "Migliorare fedelta posa/outfit"
    assert lp["notes"][0]["status"] == "aperto"


def test_backlog_set_status_e_filtro(api):
    n1 = api.add_backlog_note("qualita", "A")
    api.add_backlog_note("bug", "B")

    aperte = api.list_backlog()
    assert len(aperte["notes"]) == 2

    api.set_backlog_status(1, "fatto")

    aperte = api.list_backlog()
    assert len(aperte["notes"]) == 1
    assert aperte["notes"][0]["title"] == "B"

    tutte = api.list_backlog(status="tutti")
    assert len(tutte["notes"]) == 2


def _seed_reference(status="ready", category="BOOBS", local_video_path=None, frame_paths=None, downloaded_at=None, original_caption=None, source_url=None, download_attempts=0):
    return ReferenceItem(
        source_url=source_url or f"https://www.instagram.com/p/{status}-{category}/",
        source_tab="CAROSELLI",
        source_category=category,
        content_type_hint="carosello",
        status=status,
        local_video_path=local_video_path,
        frame_paths=frame_paths or [],
        downloaded_at=downloaded_at,
        original_caption=original_caption,
        download_attempts=download_attempts,
    )


def test_list_references_filtra_per_stato_e_categoria(api):
    with api_mod.SessionLocal() as session:
        session.add_all([
            _seed_reference(status="ready", category="BOOBS"),
            _seed_reference(status="error", category="BOOBS"),
            _seed_reference(status="ready", category="TALKING"),
        ])
        session.commit()

    tutte = api.list_references()
    assert len(tutte["references"]) == 3

    solo_ready = api.list_references(status="ready")
    assert len(solo_ready["references"]) == 2
    assert all(r["status"] == "ready" for r in solo_ready["references"])

    solo_boobs_ready = api.list_references(status="ready", category="BOOBS")
    assert len(solo_boobs_ready["references"]) == 1
    assert solo_boobs_ready["references"][0]["source_category"] == "BOOBS"


def test_list_references_ricerca_per_caption(api):
    with api_mod.SessionLocal() as session:
        session.add_all([
            _seed_reference(status="ready", category="BOOBS", original_caption="Just for curiosity"),
            _seed_reference(status="ready", category="BOOTY", original_caption="Dance with me tonight"),
        ])
        session.commit()

    r = api.list_references(search="curiosity")

    assert r["ok"]
    assert len(r["references"]) == 1
    assert r["references"][0]["original_caption"] == "Just for curiosity"


def test_list_references_ricerca_per_url_case_insensitive(api):
    with api_mod.SessionLocal() as session:
        session.add(_seed_reference(status="ready", source_url="https://www.instagram.com/p/AbCdEfG123/"))
        session.commit()

    r = api.list_references(search="abcdefg")

    assert r["ok"]
    assert len(r["references"]) == 1


def test_list_references_paginazione(api):
    with api_mod.SessionLocal() as session:
        for i in range(5):
            session.add(_seed_reference(status="ready", source_url=f"https://www.instagram.com/p/PAGE{i}/"))
        session.commit()

    prima = api.list_references(limit=2, offset=0)
    seconda = api.list_references(limit=2, offset=2)

    assert prima["ok"] and seconda["ok"]
    assert prima["total"] == 5
    assert len(prima["references"]) == 2
    assert len(seconda["references"]) == 2
    id_prima = {r["id"] for r in prima["references"]}
    id_seconda = {r["id"] for r in seconda["references"]}
    assert id_prima.isdisjoint(id_seconda)  # pagine diverse, nessuna sovrapposizione


def test_retry_reference_chiama_reference_sync(api, monkeypatch):
    seen = {}

    def fake_retry_reference(reference_id):
        seen["id"] = reference_id
        return {"id": reference_id, "status": "ready", "error_message": None}

    monkeypatch.setattr(api_mod.reference_sync, "retry_reference", fake_retry_reference)

    r = api.retry_reference(42)

    assert r["ok"]
    assert seen["id"] == 42
    assert r["retry"]["status"] == "ready"


def test_retry_all_references_seleziona_solo_stati_ritentabili(api, monkeypatch):
    with api_mod.SessionLocal() as session:
        session.add_all([
            _seed_reference(status="download_error", category="TALKING"),
            _seed_reference(status="unavailable", category="BOOBS"),
            _seed_reference(status="ready", category="BOOTY"),  # non ritentabile, va escluso
            _seed_reference(status="pending", category="GENERAL"),  # non ritentabile (lo gestisce il sync), va escluso
        ])
        session.commit()

    seen_ids = []
    monkeypatch.setattr(api_mod.reference_sync, "retry_all", lambda ids: (seen_ids.extend(ids), {"total": len(ids), "ready": len(ids), "still_failed": 0})[1])

    r = api.retry_all_references()

    assert r["ok"]
    assert len(seen_ids) == 2
    assert r["retry_all"]["total"] == 2


def test_retry_all_references_esclude_reference_con_tentativi_esauriti(api, monkeypatch):
    with api_mod.SessionLocal() as session:
        session.add_all([
            _seed_reference(status="download_error", category="TALKING", download_attempts=1),
            _seed_reference(status="unavailable", category="BOOBS", download_attempts=api_mod.reference_sync.MAX_DOWNLOAD_ATTEMPTS),
        ])
        session.commit()

    seen_ids = []
    monkeypatch.setattr(api_mod.reference_sync, "retry_all", lambda ids: (seen_ids.extend(ids), {"total": len(ids), "ready": 0, "still_failed": 0})[1])

    r = api.retry_all_references()

    assert r["ok"]
    assert len(seen_ids) == 1  # solo quella che non ha ancora esaurito i tentativi


def test_list_references_espone_tentativi_e_retryable(api):
    with api_mod.SessionLocal() as session:
        session.add_all([
            _seed_reference(status="download_error", category="TALKING", source_url="https://www.instagram.com/p/RETRYABLE/", download_attempts=1),
            _seed_reference(status="unavailable", category="BOOBS", source_url="https://www.instagram.com/p/EXHAUSTED/", download_attempts=api_mod.reference_sync.MAX_DOWNLOAD_ATTEMPTS),
        ])
        session.commit()

    r = api.list_references()
    by_url = {x["url"]: x for x in r["references"]}

    ritentabile = by_url["https://www.instagram.com/p/RETRYABLE/"]
    esaurita = by_url["https://www.instagram.com/p/EXHAUSTED/"]

    assert ritentabile["retryable"] is True
    assert ritentabile["download_attempts"] == 1
    assert esaurita["retryable"] is False
    assert esaurita["download_attempts"] == api_mod.reference_sync.MAX_DOWNLOAD_ATTEMPTS
    assert esaurita["max_download_attempts"] == api_mod.reference_sync.MAX_DOWNLOAD_ATTEMPTS


def test_retry_all_references_filtra_per_categoria(api, monkeypatch):
    with api_mod.SessionLocal() as session:
        session.add_all([
            _seed_reference(status="download_error", category="TALKING"),
            _seed_reference(status="download_error", category="BOOBS"),
        ])
        session.commit()

    seen_ids = []
    monkeypatch.setattr(api_mod.reference_sync, "retry_all", lambda ids: (seen_ids.extend(ids), {"total": len(ids), "ready": 0, "still_failed": 0})[1])

    r = api.retry_all_references(category="TALKING")

    assert r["ok"]
    assert len(seen_ids) == 1


def test_open_reference_folder_reference_inesistente(api):
    r = api.open_reference_folder(999)
    assert r["ok"] is False
    assert "inesistente" in r["error"]


def test_open_reference_folder_senza_media_locale(api):
    with api_mod.SessionLocal() as session:
        ref = _seed_reference(status="pending")
        session.add(ref)
        session.commit()
        ref_id = ref.id

    r = api.open_reference_folder(ref_id)
    assert r["ok"] is False
    assert "cartella locale" in r["error"]


def test_open_reference_folder_apre_cartella_dentro_media_dir(api, monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    folder = media_root / "2026-W29" / "CAROSELLI" / "BOOBS" / "ABC123"
    folder.mkdir(parents=True)
    video = folder / "video.mp4"
    video.write_bytes(b"finto")

    monkeypatch.setattr(api_mod.config, "MEDIA_DIR", media_root)

    with api_mod.SessionLocal() as session:
        ref = _seed_reference(status="ready", local_video_path=str(video))
        session.add(ref)
        session.commit()
        ref_id = ref.id

    calls = []
    monkeypatch.setattr(api_mod.subprocess, "run", lambda args, **kw: calls.append(args))

    r = api.open_reference_folder(ref_id)

    assert r["ok"]
    assert r["folder"] == str(folder)
    assert calls == [["open", str(folder)]]


def test_open_reference_folder_rifiuta_percorso_fuori_media_dir(api, monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    outside = tmp_path / "altrove"
    outside.mkdir()
    video = outside / "video.mp4"
    video.write_bytes(b"finto")

    monkeypatch.setattr(api_mod.config, "MEDIA_DIR", media_root)

    with api_mod.SessionLocal() as session:
        ref = _seed_reference(status="ready", local_video_path=str(video))
        session.add(ref)
        session.commit()
        ref_id = ref.id

    r = api.open_reference_folder(ref_id)

    assert r["ok"] is False
    assert "sicurezza" in r["error"]


def test_list_content_pieces_filtra_per_stato(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        session.add_all([
            ContentPiece(profile=profile, content_type="carosello", status="delivered", cost_credits_actual=0.36),
            ContentPiece(profile=profile, content_type="video_talking", status="error"),
        ])
        session.commit()

    tutti = api.list_content_pieces()
    assert len(tutti["pieces"]) == 2

    consegnati = api.list_content_pieces(status="delivered")
    assert len(consegnati["pieces"]) == 1
    assert consegnati["pieces"][0]["content_type"] == "carosello"
    assert consegnati["pieces"][0]["profile_nome"] == "Ruby"
    assert consegnati["pieces"][0]["cost_credits_actual"] == 0.36


def test_piece_timeline_ritorna_eventi_in_ordine(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="delivered")
        session.add(piece)
        session.commit()
        session.add_all([
            ContentPieceEvent(content_piece_id=piece.id, stage="image_regen", status="started"),
            ContentPieceEvent(content_piece_id=piece.id, stage="image_regen", status="completed", duration_seconds=1.5),
        ])
        session.commit()
        piece_id = piece.id

    r = api.piece_timeline(piece_id)

    assert r["ok"]
    assert r["piece"]["id"] == piece_id
    assert [e["status"] for e in r["events"]] == ["started", "completed"]
    assert r["events"][1]["duration_seconds"] == 1.5


def test_piece_timeline_id_inesistente(api):
    r = api.piece_timeline(999)
    assert r["ok"] is False
    assert "inesistente" in r["error"]


def test_duplicate_plan_endpoint_copia_griglia_su_nuova_settimana(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    pl = api.create_plan(1, "2026-07-20", "2026-07-26")
    plan_id = pl["plan"]["id"]
    api.plan_set_cell(plan_id, "carosello", "lun", 2)

    r = api.duplicate_plan(plan_id, "2026-07-27", "2026-08-02")

    assert r["ok"]
    new_plan = r["plan"]
    assert new_plan["id"] != plan_id
    assert new_plan["week_start"] == "2026-07-27"
    assert new_plan["status"] == "bozza"
    assert new_plan["grid"]["carosello"]["lun"] == 2
    assert new_plan["assigned_references"] == 0  # nessuna reference copiata


def test_duplicate_plan_piano_inesistente(api):
    r = api.duplicate_plan(999, "2026-07-27", "2026-08-02")
    assert r["ok"] is False
    assert "inesistente" in r["error"]


def test_monthly_summary_aggrega_le_settimane_del_mese(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    pl1 = api.create_plan(1, "2026-07-06", "2026-07-12")
    api.plan_set_cell(pl1["plan"]["id"], "carosello", "lun", 2)
    pl2 = api.create_plan(1, "2026-07-20", "2026-07-26")
    api.plan_set_cell(pl2["plan"]["id"], "video_talking", "mer", 1)
    # fuori dal mese di luglio: non deve comparire
    pl3 = api.create_plan(1, "2026-08-03", "2026-08-09")
    api.plan_set_cell(pl3["plan"]["id"], "carosello", "lun", 5)

    r = api.monthly_summary(1, 2026, 7)

    assert r["ok"]
    assert len(r["weeks"]) == 2
    assert r["total_pieces"] == 3
    assert r["totals_by_type"] == {"carosello": 2, "video_talking": 1}


def test_list_profiles_include_statistiche_produzione(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        session.add_all([
            ContentPiece(profile=profile, content_type="carosello", status="delivered", cost_credits_actual=0.36),
            ContentPiece(profile=profile, content_type="video_talking", status="delivered", cost_credits_actual=36.0),
            ContentPiece(profile=profile, content_type="video_balletti", status="error"),
        ])
        session.commit()

    r = api.list_profiles()

    assert r["ok"]
    stats = r["profiles"][0]["content_stats"]
    assert stats["total"] == 3
    assert stats["delivered"] == 2
    assert stats["cost_actual"] == pytest.approx(36.36)


def test_reference_weekly_trend_aggrega_per_settimana(api):
    with api_mod.SessionLocal() as session:
        session.add_all([
            _seed_reference(status="ready", category="TALKING"),
            _seed_reference(status="error", category="BOOBS"),
        ])
        # forza settimane diverse esplicitamente
        refs = session.query(ReferenceItem).all()
        refs[0].week_start = dt.date(2026, 7, 6)
        refs[1].week_start = dt.date(2026, 7, 13)
        session.commit()

    r = api.reference_weekly_trend(weeks=8)

    assert r["ok"]
    weeks = {w["week_start"]: w for w in r["weeks"]}
    assert weeks["2026-07-06"]["ready"] == 1
    assert weeks["2026-07-13"]["error"] == 1
    # ordine cronologico, non decrescente
    assert [w["week_start"] for w in r["weeks"]] == sorted(weeks.keys())


def test_ledger_history_ritorna_voci_recenti_con_content_type(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="delivered")
        session.add(piece)
        session.commit()
        ledger.record_topup(session, credits=100.0)
        ledger.record_consumption(session, credits=0.36, motivo="image_regen", content_piece_id=piece.id)
        session.commit()

    r = api.ledger_history(limit=10)

    assert r["ok"]
    assert len(r["entries"]) == 2
    consumo = next(e for e in r["entries"] if e["delta_credits"] < 0)
    assert consumo["content_type"] == "carosello"
    assert consumo["motivo"] == "image_regen"


def test_spend_by_content_type_aggrega_solo_i_consumi(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        p1 = ContentPiece(profile=profile, content_type="carosello", status="delivered")
        p2 = ContentPiece(profile=profile, content_type="video_talking", status="delivered")
        session.add_all([p1, p2])
        session.commit()
        ledger.record_topup(session, credits=100.0)
        ledger.record_consumption(session, credits=0.36, motivo="image_regen", content_piece_id=p1.id)
        ledger.record_consumption(session, credits=36.0, motivo="video_regen", content_piece_id=p2.id)
        session.commit()

    r = api.spend_by_content_type()

    assert r["ok"]
    assert r["totals"] == {"carosello": pytest.approx(0.36), "video_talking": pytest.approx(36.0)}
    assert "ricarica" not in r["totals"]  # la ricarica (delta positivo) non e' un consumo


def test_monthly_projection_calcola_media_giornaliera(api, monkeypatch):
    fixed_now = dt.datetime(2026, 7, 15, 12, 0, 0)

    class FixedDatetime(dt.datetime):
        @classmethod
        def utcnow(cls):
            return fixed_now

    monkeypatch.setattr(api_mod.dt, "datetime", FixedDatetime)

    with api_mod.SessionLocal() as session:
        ledger.record_topup(session, credits=100.0)
        ledger.record_consumption(session, credits=14.0, motivo="test")
        session.commit()

    r = api.monthly_projection(window_days=14)

    assert r["ok"]
    assert r["spent_in_window"] == pytest.approx(14.0)
    assert r["daily_avg"] == pytest.approx(1.0)
    assert r["projected_30_days"] == pytest.approx(30.0)


def test_list_references_thumbnail_carosello_e_foto_diretta(api, tmp_path):
    foto = tmp_path / "foto.jpg"
    foto.write_bytes(b"finta immagine")
    with api_mod.SessionLocal() as session:
        session.add(_seed_reference(status="ready", category="BOOBS", frame_paths=[str(foto)]))
        session.commit()

    r = api.list_references()

    assert r["ok"]
    assert r["references"][0]["thumbnail_url"] == f"file://{foto}"


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg non disponibile in questo ambiente")
def test_list_references_thumbnail_video_genera_e_mette_in_cache(api, tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video)
    with api_mod.SessionLocal() as session:
        session.add(_seed_reference(status="ready", category="TALKING", local_video_path=str(video)))
        session.commit()

    r = api.list_references()

    assert r["ok"]
    thumb_url = r["references"][0]["thumbnail_url"]
    assert thumb_url is not None
    thumb_path = thumb_url.replace("file://", "")
    assert thumb_path.endswith("_thumb.jpg")
    import os
    assert os.path.exists(thumb_path)

    # secondo giro: usa la cache, non rigenera
    mtime_before = os.path.getmtime(thumb_path)
    api.list_references()
    assert os.path.getmtime(thumb_path) == mtime_before


def test_list_content_pieces_include_thumbnail(api, tmp_path):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    foto = tmp_path / "asset_01.png"
    foto.write_bytes(b"finta")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        session.add(ContentPiece(
            profile=profile, content_type="carosello", status="delivered",
            generated_assets=[str(foto)],
        ))
        session.commit()

    r = api.list_content_pieces()

    assert r["ok"]
    assert r["pieces"][0]["thumbnail_url"] == f"file://{foto}"
    assert r["pieces"][0]["has_output"] is True


def test_open_piece_folder_reference_inesistente(api):
    r = api.open_piece_folder(999)
    assert r["ok"] is False
    assert "inesistente" in r["error"]


def test_open_piece_folder_senza_output(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="error")
        session.add(piece)
        session.commit()
        piece_id = piece.id

    r = api.open_piece_folder(piece_id)
    assert r["ok"] is False
    assert "Nessun file locale" in r["error"]


def test_open_piece_folder_apre_cartella_dentro_delivery_dir(api, monkeypatch, tmp_path):
    delivery_root = tmp_path / "delivery"
    folder = delivery_root / "ruby-wilde" / "carosello" / "2026-07-20_lun_1"
    folder.mkdir(parents=True)
    asset = folder / "asset_01.png"
    asset.write_bytes(b"finta")
    monkeypatch.setattr(api_mod.config, "DELIVERY_DIR", delivery_root)

    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="delivered", generated_assets=[str(asset)])
        session.add(piece)
        session.commit()
        piece_id = piece.id

    calls = []
    monkeypatch.setattr(api_mod.subprocess, "run", lambda args, **kw: calls.append(args))

    r = api.open_piece_folder(piece_id)

    assert r["ok"]
    assert r["folder"] == str(folder)
    assert calls == [["open", str(folder)]]


def test_health_check_reporta_binari_e_credenziali(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api_mod.shutil, "which", lambda name: "/usr/bin/fake" if name == "higgsfield" else None)
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    monkeypatch.setattr(api_mod.config, "HIGGSFIELD_CLI_BIN", "higgsfield")
    monkeypatch.setattr(api_mod.config, "CLAUDE_CLI_BIN", "claude")
    monkeypatch.setattr(api_mod.config, "GOOGLE_SERVICE_ACCOUNT_FILE", str(creds))

    r = api.health_check()

    assert r["ok"]
    assert r["higgsfield_cli"] is True
    assert r["claude_cli"] is False
    assert r["google_sheet_credentials"] is True
    assert r["all_ok"] is False


def test_global_search_trova_reference_pezzi_e_backlog(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    api.add_backlog_note("qualita", "Fedelta posa carosello da migliorare", "vedi test dedicato")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        session.add_all([
            ReferenceItem(source_url="https://www.instagram.com/p/SEARCH/", status="ready", original_caption="una fedelta incredibile"),
            ContentPiece(profile=profile, content_type="carosello", status="delivered", caption="fedelta al top"),
        ])
        session.commit()

    r = api.global_search("fedelta")

    assert r["ok"]
    assert len(r["references"]) == 1
    assert len(r["pieces"]) == 1
    assert len(r["backlog"]) == 1


def test_global_search_query_vuota_ritorna_liste_vuote(api):
    r = api.global_search("   ")
    assert r["ok"]
    assert r["references"] == [] and r["pieces"] == [] and r["backlog"] == []


def test_today_events_solo_eventi_di_oggi(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="delivered")
        session.add(piece)
        session.commit()
        session.add_all([
            ContentPieceEvent(content_piece_id=piece.id, stage="image_regen", status="completed", duration_seconds=1.2),
            ContentPieceEvent(content_piece_id=piece.id, stage="qa", status="completed", duration_seconds=0.5, timestamp=dt.datetime.utcnow() - dt.timedelta(days=3)),
        ])
        session.commit()

    r = api.today_events()

    assert r["ok"]
    assert len(r["events"]) == 1
    assert r["events"][0]["stage"] == "image_regen"
    assert r["events"][0]["profile_nome"] == "Ruby"


def test_retry_content_piece_endpoint_chiama_engine(api, monkeypatch):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="error")
        session.add(piece)
        session.commit()
        piece_id = piece.id

    seen = {}

    def fake_retry(session, pid):
        seen["piece_id"] = pid
        return {"id": pid, "status": "delivered"}

    monkeypatch.setattr(api_mod.production_engine, "retry_content_piece", fake_retry)

    r = api.retry_content_piece(piece_id)

    assert r["ok"]
    assert seen["piece_id"] == piece_id
    assert r["retry"]["status"] == "delivered"


def test_set_piece_quality_valida_range(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="delivered")
        session.add(piece)
        session.commit()
        piece_id = piece.id

    fuori_range = api.set_piece_quality(piece_id, 9)
    assert fuori_range["ok"] is False

    ok = api.set_piece_quality(piece_id, 4)
    assert ok["ok"]
    assert ok["quality_rating"] == 4
    with api_mod.SessionLocal() as session:
        assert session.get(ContentPiece, piece_id).quality_rating == 4


def test_bump_piece_priority_assegna_massimo_piu_uno(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        basso = ContentPiece(profile=profile, content_type="carosello", status="reference_ready", priority=0)
        alto = ContentPiece(profile=profile, content_type="carosello", status="reference_ready", priority=3)
        session.add_all([basso, alto])
        session.commit()
        basso_id = basso.id

    r = api.bump_piece_priority(basso_id)

    assert r["ok"]
    assert r["priority"] == 4


def test_plan_allocation_preview_non_modifica_nulla(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    plan_id = api.create_plan(1, "2026-07-20", "2026-07-26")["plan"]["id"]
    api.plan_set_cell(plan_id, "carosello", "lun", 1)
    with api_mod.SessionLocal() as session:
        session.add(ReferenceItem(
            source_url="https://www.instagram.com/p/ALLOC/", source_tab="CAROSELLI", source_category="BOOBS",
            content_type_hint="carosello", week_start=dt.date(2026, 7, 13), week_end=dt.date(2026, 7, 19),
            status="ready", frame_paths=["/tmp/a.jpg"],
        ))
        session.commit()

    r = api.plan_allocation_preview(plan_id)

    assert r["ok"]
    assert r["would_assign"] == 1
    assert r["would_miss"] == 0
    assert r["pieces"][0]["reference_category"] == "BOOBS"
    # nessuna modifica salvata: la reference resta non assegnata
    with api_mod.SessionLocal() as session:
        piece = session.query(ContentPiece).filter(ContentPiece.plan_week_id == plan_id).one()
        assert piece.reference_id is None


def test_import_reference_url_endpoint(api, monkeypatch):
    seen = {}

    def fake_import(url, *, source_tab, source_category, content_type_hint):
        seen.update(url=url, source_tab=source_tab, source_category=source_category, content_type_hint=content_type_hint)
        return {"id": 1, "status": "ready", "error_message": None}

    monkeypatch.setattr(api_mod.reference_sync, "import_single_reference", fake_import)

    r = api.import_reference_url("https://www.instagram.com/p/X/", "CAROSELLI", "BOOBS", "carosello")

    assert r["ok"]
    assert seen == {
        "url": "https://www.instagram.com/p/X/", "source_tab": "CAROSELLI",
        "source_category": "BOOBS", "content_type_hint": "carosello",
    }
    assert r["import"]["status"] == "ready"


def test_cost_estimate_vs_actual_aggrega_per_tipo_e_ignora_incompleti(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        session.add_all([
            ContentPiece(profile=profile, content_type="video_balletti", status="delivered", cost_credits_estimated=16.0, cost_credits_actual=18.0),
            ContentPiece(profile=profile, content_type="video_balletti", status="delivered", cost_credits_estimated=16.0, cost_credits_actual=18.0),
            ContentPiece(profile=profile, content_type="carosello", status="delivered", cost_credits_estimated=0.36),  # solo stima, escluso
        ])
        session.commit()

    r = api.cost_estimate_vs_actual()

    assert r["ok"]
    assert "carosello" not in r["by_content_type"]
    b = r["by_content_type"]["video_balletti"]
    assert b["count"] == 2
    assert b["estimated"] == 32.0
    assert b["actual"] == 36.0
    assert b["delta"] == 4.0


def test_scheduler_status_legge_i_log(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api_mod.config, "DATA_DIR", tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "weekly-reference-sync.out.log").write_text("riga1\nriga2\n")

    r = api.scheduler_status()

    assert r["ok"]
    assert r["out"] is not None
    assert "riga2" in r["out"]["tail"]
    assert r["err"] is None


def test_character_history_filtra_per_creator(api):
    with api_mod.SessionLocal() as session:
        session.add_all([
            CharacterVersion(creator_nome="Ruby", physical_description="v1", mandatory_additions="m", negative_prompt="n"),
            CharacterVersion(creator_nome="Ruby", physical_description="v2", mandatory_additions="m", negative_prompt="n"),
            CharacterVersion(creator_nome="Altra", physical_description="v1", mandatory_additions="m", negative_prompt="n"),
        ])
        session.commit()

    tutte = api.character_history()
    assert len(tutte["versions"]) == 3

    solo_ruby = api.character_history("Ruby")
    assert len(solo_ruby["versions"]) == 2
    assert solo_ruby["versions"][0]["physical_description"] == "v2"  # piu' recente prima


def test_stage_duration_stats_calcola_media_solo_su_completati(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        piece = ContentPiece(profile=profile, content_type="carosello", status="delivered")
        session.add(piece)
        session.commit()
        session.add_all([
            ContentPieceEvent(content_piece_id=piece.id, stage="image_regen", status="completed", duration_seconds=2.0),
            ContentPieceEvent(content_piece_id=piece.id, stage="image_regen", status="completed", duration_seconds=4.0),
            ContentPieceEvent(content_piece_id=piece.id, stage="image_regen", status="started"),  # ignorato: nessuna duration
        ])
        session.commit()

    r = api.stage_duration_stats()

    assert r["ok"]
    stage = next(s for s in r["stages"] if s["stage"] == "image_regen")
    assert stage["count"] == 2
    assert stage["avg_seconds"] == 3.0


def test_list_backlog_ricerca_per_testo(api):
    api.add_backlog_note("qualita", "Fedelta posa carosello", "descrizione")
    api.add_backlog_note("funzionalita", "Export CSV", "")

    r = api.list_backlog(status="tutti", search="fedelta")

    assert r["ok"]
    assert len(r["notes"]) == 1
    assert r["notes"][0]["title"] == "Fedelta posa carosello"


def test_budget_status_espone_alert_soglia(api, monkeypatch):
    monkeypatch.setattr(api_mod.config, "BUDGET_ALERT_THRESHOLD", 50.0)

    basso = api.budget_status()
    assert basso["budget_alert"] is True
    assert basso["budget_alert_threshold"] == 50.0

    api.budget_topup(100.0)
    alto = api.budget_status()
    assert alto["budget_alert"] is False


def test_run_backup_endpoint_passa_dal_modulo_backup(api, monkeypatch):
    monkeypatch.setattr(api_mod.backup, "run_backup", lambda: {"ok": True, "path": "/tmp/finto.db", "kept": 1, "removed": 0})

    r = api.run_backup()

    assert r["ok"]
    assert r["path"] == "/tmp/finto.db"


def test_production_preview_espone_dettaglio_pezzi(api):
    api.create_creator("Trinity")
    api.create_profile(1, "Ruby", "misto")
    api.budget_topup(100.0)
    with api_mod.SessionLocal() as session:
        profile = session.query(Profile).one()
        plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26), status="approvato")
        ref = ReferenceItem(
            source_url="https://www.instagram.com/p/DRY/", source_tab="CAROSELLI", source_category="BOOBS",
            content_type_hint="carosello", status="ready", frame_paths=["/tmp/a.jpg"],
        )
        piece = ContentPiece(profile=profile, plan_week=plan, reference=ref, content_type="carosello", status="reference_ready")
        session.add_all([plan, ref, piece])
        session.commit()

    r = api.production_preview()

    assert r["ok"]
    assert len(r["pieces"]) == 1
    assert r["pieces"][0]["content_type"] == "carosello"
    assert r["pieces"][0]["reference_category"] == "BOOBS"
    assert r["pieces"][0]["estimated_cost"] == pytest.approx(r["estimated_cost"])
