"""Test del layer di reporting (Command Center) — read-only, dati reali dal DB."""

import datetime as dt

from aicraft import config, reporting
from aicraft.budget import ledger
from aicraft.db.models import ContentPiece, Creator, PlanWeek, Profile, ReferenceItem


def test_overview_db_vuoto(db_session):
    ov = reporting.overview(db_session)
    assert ov["saldo_crediti"] == 0.0
    assert ov["profili"] == []
    assert ov["reference_per_stato"] == {}
    assert ov["piani_per_stato"] == {}
    # format non deve sollevare
    assert "Command Center" in reporting.format_overview(ov)


def test_overview_aggrega_stati(db_session):
    creator = Creator(nome="Trinity")
    profile = Profile(creator=creator, nome="Ruby", tipo_contenuto="misto")
    plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26), status="approvato")
    r1 = ReferenceItem(source_url="u1", status="ready")
    r2 = ReferenceItem(source_url="u2", status="ready")
    r3 = ReferenceItem(source_url="u3", status="error")
    c1 = ContentPiece(profile=profile, content_type="carosello", plan_week=plan, status="delivered")
    c2 = ContentPiece(profile=profile, content_type="video_talking", plan_week=plan, status="reference_ready")
    db_session.add_all([creator, profile, plan, r1, r2, r3, c1, c2])
    db_session.commit()
    ledger.record_topup(db_session, credits=100.0)
    db_session.commit()

    ov = reporting.overview(db_session)

    assert ov["saldo_crediti"] == 100.0
    assert len(ov["profili"]) == 1
    assert ov["profili"][0]["nome"] == "Ruby"
    assert ov["reference_per_stato"] == {"ready": 2, "error": 1}
    assert ov["piani_per_stato"] == {"approvato": 1}
    assert ov["content_per_stato"] == {"delivered": 1, "reference_ready": 1}

    testo = reporting.format_overview(ov)
    assert "Ruby" in testo
    assert "100.00" in testo


def test_overview_segnala_budget_alert_sotto_soglia(db_session, monkeypatch):
    monkeypatch.setattr(config, "BUDGET_ALERT_THRESHOLD", 50.0)

    ov = reporting.overview(db_session)
    assert ov["budget_alert"] is True
    assert ov["budget_alert_threshold"] == 50.0

    ledger.record_topup(db_session, credits=100.0)
    db_session.commit()

    ov = reporting.overview(db_session)
    assert ov["budget_alert"] is False
