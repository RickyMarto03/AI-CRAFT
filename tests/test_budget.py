"""Test del modulo Budget. cost_fn iniettabile: nessuna dipendenza da
Higgsfield/rete/credenziali."""

import datetime as dt

import pytest

from aicraft.budget import estimate, ledger
from aicraft.db.models import ContentPiece, Creator, PlanWeek, Profile


def _fake_cost_fn(job_type, params):
    # costi finti deterministici per (modello, params)
    table = {"text2image_soul_v2": 0.12, "seedance_2_0": 10.0}
    base = table[job_type]
    if params.get("duration"):
        return base  # kling3_0 con duration=5 -> 10.0
    return base


# --- ledger: saldo come somma cumulativa ---

def test_saldo_iniziale_zero(db_session):
    assert ledger.current_balance(db_session) == 0.0


def test_saldo_somma_topup_e_consumi(db_session):
    ledger.record_topup(db_session, credits=100.0)
    ledger.record_consumption(db_session, credits=30.0, motivo="test")
    ledger.record_consumption(db_session, credits=10.5, motivo="test")
    db_session.commit()
    assert ledger.current_balance(db_session) == 59.5


def test_consumo_sempre_negativo_anche_se_passato_positivo(db_session):
    ledger.record_topup(db_session, credits=50.0)
    entry = ledger.record_consumption(db_session, credits=20.0, motivo="test")
    db_session.commit()
    assert entry.delta_credits == -20.0
    assert ledger.current_balance(db_session) == 30.0


# --- estimate ---

def test_stima_content_type_somma_ops(db_session):
    # video_talking = 1 immagine (0.12) + 1 video (10.0) = 10.12
    cost = estimate.estimate_content_type("video_talking", cost_fn=_fake_cost_fn)
    assert cost == 10.12


def test_stima_carosello_solo_immagine(db_session):
    # count=3 (stima conservativa: numero massimo di foto per carosello,
    # vedi pipeline_spec.py) x 0.12 = 0.36
    cost = estimate.estimate_content_type("carosello", cost_fn=_fake_cost_fn)
    assert cost == pytest.approx(0.36)


def test_stima_piano_somma_pezzi_e_persiste(db_session):
    creator = Creator(nome="C")
    profile = Profile(creator=creator, nome="Ruby", tipo_contenuto="misto")
    plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26))
    p1 = ContentPiece(profile=profile, content_type="video_talking", plan_week=plan, status="reference_ready")
    p2 = ContentPiece(profile=profile, content_type="carosello", plan_week=plan, status="reference_ready")
    db_session.add_all([creator, profile, plan, p1, p2])
    db_session.commit()

    total = estimate.estimate_plan(db_session, plan, cost_fn=_fake_cost_fn, persist=True)

    assert total == pytest.approx(10.12 + 0.36)
    assert p1.cost_credits_estimated == pytest.approx(10.12)
    assert p2.cost_credits_estimated == pytest.approx(0.36)


def test_stima_usa_cache_per_ops_ripetute(db_session):
    calls = {"n": 0}

    def counting_cost_fn(job_type, params):
        calls["n"] += 1
        return 1.0

    creator = Creator(nome="C")
    profile = Profile(creator=creator, nome="Ruby", tipo_contenuto="misto")
    plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26))
    # 3 pezzi video_talking: stessi 2 op (image+video) -> deve interrogare cost_fn solo 2 volte
    for _ in range(3):
        db_session.add(ContentPiece(profile=profile, content_type="video_talking", plan_week=plan, status="reference_ready"))
    db_session.add_all([creator, profile, plan])
    db_session.commit()

    estimate.estimate_plan(db_session, plan, cost_fn=counting_cost_fn, persist=False)

    assert calls["n"] == 2  # (text2image_soul_v2, ()) e (kling3_0, (duration,5))
