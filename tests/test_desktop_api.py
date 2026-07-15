"""Test del bridge API dell'app desktop, senza GUI e senza rete (stima costi
finta). Verifica che i metodi chiamabili dal frontend ritornino i dati reali
attesi e gestiscano gli errori come {ok: False}."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft.budget import ledger
from aicraft.db.base import Base
from aicraft.desktop import api as api_mod
from aicraft.production import higgsfield_client


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
    prev = api.production_preview()
    assert prev["ready_count"] == 2
    assert prev["estimated_cost"] == pytest.approx(0.72)  # 2 pezzi x (count=3 x 0.12)


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
