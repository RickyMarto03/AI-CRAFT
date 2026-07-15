"""Test del modulo Planning + integrazione col Budget all'approvazione."""

import datetime as dt

import pytest

from aicraft.budget import ledger
from aicraft.budget.errors import BudgetInsufficientError
from aicraft.db.models import Creator, Profile
from aicraft.planning import plan as planning
from aicraft.planning.quota import QuotaExceededError, QuotaPolicy


def _fake_cost_fn(job_type, params):
    return {"text2image_soul_v2": 0.12, "seedance_2_0": 10.0}[job_type]


@pytest.fixture
def profile(db_session):
    creator = Creator(nome="C")
    prof = Profile(creator=creator, nome="Ruby Wilde", tipo_contenuto="misto")
    db_session.add_all([creator, prof])
    db_session.commit()
    return prof


def _new_plan(db_session, profile):
    return planning.create_plan_week(
        db_session, profile_id=profile.id, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26)
    )


# --- creazione e versioning ---

def test_nuovo_piano_e_bozza_versione_1(db_session, profile):
    plan = _new_plan(db_session, profile)
    assert plan.status == "bozza"
    assert plan.version == 1


def test_aggiungere_pezzo_incrementa_versione(db_session, profile):
    plan = _new_plan(db_session, profile)
    planning.add_content_piece(db_session, plan, content_type="carosello", scheduled_day="lun")
    assert plan.version == 2
    planning.add_content_piece(db_session, plan, content_type="video_talking", scheduled_day="mar")
    assert plan.version == 3


def test_rimuovere_pezzo_incrementa_versione(db_session, profile):
    plan = _new_plan(db_session, profile)
    piece = planning.add_content_piece(db_session, plan, content_type="carosello", scheduled_day="lun")
    v = plan.version
    planning.remove_content_piece(db_session, plan, piece)
    assert plan.version == v + 1


def test_scheduled_day_non_valido_rifiutato(db_session, profile):
    plan = _new_plan(db_session, profile)
    with pytest.raises(ValueError):
        planning.add_content_piece(db_session, plan, content_type="carosello", scheduled_day="lunedi")


# --- quote ---

def test_quota_giornaliera_blocca_terzo_pezzo(db_session, profile):
    plan = _new_plan(db_session, profile)
    policy = QuotaPolicy(max_pezzi_per_giorno=2)
    planning.add_content_piece(db_session, plan, content_type="carosello", scheduled_day="lun", policy=policy)
    planning.add_content_piece(db_session, plan, content_type="stories", scheduled_day="lun", policy=policy)
    with pytest.raises(QuotaExceededError):
        planning.add_content_piece(db_session, plan, content_type="video_talking", scheduled_day="lun", policy=policy)
    # su un altro giorno invece passa
    planning.add_content_piece(db_session, plan, content_type="video_talking", scheduled_day="mar", policy=policy)


def test_quota_per_tipo_settimana(db_session, profile):
    plan = _new_plan(db_session, profile)
    policy = QuotaPolicy(max_per_tipo_settimana={"carosello": 1})
    planning.add_content_piece(db_session, plan, content_type="carosello", scheduled_day="lun", policy=policy)
    with pytest.raises(QuotaExceededError):
        planning.add_content_piece(db_session, plan, content_type="carosello", scheduled_day="mar", policy=policy)


# --- approvazione + budget ---

def test_approvazione_bloccata_se_saldo_insufficiente(db_session, profile):
    plan = _new_plan(db_session, profile)
    planning.add_content_piece(db_session, plan, content_type="video_talking", scheduled_day="lun")  # 10.12
    ledger.record_topup(db_session, credits=5.0)  # saldo insufficiente
    db_session.commit()

    with pytest.raises(BudgetInsufficientError) as exc:
        planning.approve_plan(db_session, plan, cost_fn=_fake_cost_fn)
    assert exc.value.needed == pytest.approx(10.12)
    assert exc.value.available == 5.0
    assert plan.status == "bozza"  # resta in bozza


def test_approvazione_ok_se_saldo_sufficiente(db_session, profile):
    plan = _new_plan(db_session, profile)
    piece = planning.add_content_piece(db_session, plan, content_type="video_talking", scheduled_day="lun")
    ledger.record_topup(db_session, credits=100.0)
    db_session.commit()

    estimated = planning.approve_plan(db_session, plan, cost_fn=_fake_cost_fn)

    assert plan.status == "approvato"
    assert estimated == pytest.approx(10.12)
    assert piece.cost_credits_estimated == pytest.approx(10.12)


def test_modifica_a_piano_approvato_lo_riporta_in_bozza(db_session, profile):
    plan = _new_plan(db_session, profile)
    planning.add_content_piece(db_session, plan, content_type="carosello", scheduled_day="lun")
    ledger.record_topup(db_session, credits=100.0)
    db_session.commit()
    planning.approve_plan(db_session, plan, cost_fn=_fake_cost_fn)
    assert plan.status == "approvato"

    # aggiungere un pezzo dopo l'approvazione richiede nuova approvazione
    planning.add_content_piece(db_session, plan, content_type="video_talking", scheduled_day="mar")
    assert plan.status == "bozza"


# --- duplicazione settimana ---

def test_duplicate_plan_week_copia_la_griglia(db_session, profile):
    source = _new_plan(db_session, profile)
    planning.add_content_piece(db_session, source, content_type="carosello", scheduled_day="lun")
    planning.add_content_piece(db_session, source, content_type="carosello", scheduled_day="lun")
    planning.add_content_piece(db_session, source, content_type="video_talking", scheduled_day="mer")
    db_session.commit()

    new_plan = planning.duplicate_plan_week(
        db_session, source, week_start=dt.date(2026, 7, 27), week_end=dt.date(2026, 8, 2)
    )
    db_session.commit()

    assert new_plan.id != source.id
    assert new_plan.status == "bozza"
    assert new_plan.week_start == dt.date(2026, 7, 27)

    pieces = [(p.content_type, p.scheduled_day) for p in new_plan.content_pieces]
    assert sorted(pieces) == sorted([
        ("carosello", "lun"), ("carosello", "lun"), ("video_talking", "mer"),
    ])
    # i pezzi duplicati non hanno reference assegnata: la nuova settimana la pesca fresca
    assert all(p.reference_id is None for p in new_plan.content_pieces)


def test_duplicate_plan_week_piano_vuoto_non_esplode(db_session, profile):
    source = _new_plan(db_session, profile)
    db_session.commit()

    new_plan = planning.duplicate_plan_week(
        db_session, source, week_start=dt.date(2026, 7, 27), week_end=dt.date(2026, 8, 2)
    )

    assert new_plan.content_pieces == []
