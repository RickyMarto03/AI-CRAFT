"""Test del bridge API dell'app desktop, senza GUI e senza rete (stima costi
finta). Verifica che i metodi chiamabili dal frontend ritornino i dati reali
attesi e gestiscano gli errori come {ok: False}."""

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft.budget import ledger
from aicraft.db.base import Base
from aicraft.db.models import ContentPiece, PlanWeek, Profile, ReferenceItem
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
