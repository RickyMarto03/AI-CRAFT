"""Test del bridge API dell'app desktop, senza GUI e senza rete (stima costi
finta). Verifica che i metodi chiamabili dal frontend ritornino i dati reali
attesi e gestiscano gli errori come {ok: False}."""

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft.budget import ledger
from aicraft.db.base import Base
from aicraft.db.models import ContentPiece, ContentPieceEvent, PlanWeek, Profile, ReferenceItem
from aicraft.desktop import api as api_mod
from aicraft.production import higgsfield_client
from aicraft.reference_sync import sync as reference_sync


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
        session.commit()

    r = api.reference_stats()

    assert r["ok"]
    assert r["ready"] == 1
    assert r["by_week"]["2026-07-13"] == 1
    assert r["by_category"]["CAROSELLI / BOOBS"] == 1
    assert r["latest"][0]["has_caption"] is True


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


def _seed_reference(status="ready", category="BOOBS", local_video_path=None, frame_paths=None, downloaded_at=None):
    return ReferenceItem(
        source_url=f"https://www.instagram.com/p/{status}-{category}/",
        source_tab="CAROSELLI",
        source_category=category,
        content_type_hint="carosello",
        status=status,
        local_video_path=local_video_path,
        frame_paths=frame_paths or [],
        downloaded_at=downloaded_at,
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
