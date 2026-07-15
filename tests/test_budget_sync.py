"""Test del sync saldo Higgsfield -> CreditLedger. account_status_fn finto:
nessuna dipendenza da rete/credenziali."""

from aicraft.budget import ledger, sync


def test_sync_da_ledger_vuoto_registra_saldo_reale(db_session):
    result = sync.sync_from_higgsfield(db_session, account_status_fn=lambda: {"credits": 651.25})
    db_session.commit()

    assert result["real"] == 651.25
    assert result["internal_before"] == 0.0
    assert result["adjustment"] == 651.25
    assert ledger.current_balance(db_session) == 651.25


def test_sync_registra_solo_la_differenza(db_session):
    ledger.record_topup(db_session, credits=600.0)
    db_session.commit()

    result = sync.sync_from_higgsfield(db_session, account_status_fn=lambda: {"credits": 651.25})
    db_session.commit()

    assert result["internal_before"] == 600.0
    assert result["adjustment"] == 651.25 - 600.0
    assert ledger.current_balance(db_session) == 651.25


def test_sync_gia_allineato_non_crea_voci(db_session):
    from aicraft.db.models import CreditLedger

    ledger.record_topup(db_session, credits=651.25)
    db_session.commit()

    n_prima = db_session.query(CreditLedger).count()
    sync.sync_from_higgsfield(db_session, account_status_fn=lambda: {"credits": 651.25})
    db_session.commit()
    n_dopo = db_session.query(CreditLedger).count()

    assert n_dopo == n_prima  # nessuna voce di rettifica
    assert ledger.current_balance(db_session) == 651.25


def test_sync_saldo_reale_diminuito_registra_rettifica_negativa(db_session):
    ledger.record_topup(db_session, credits=651.25)
    db_session.commit()

    result = sync.sync_from_higgsfield(db_session, account_status_fn=lambda: {"credits": 640.0})
    db_session.commit()

    assert result["adjustment"] == 640.0 - 651.25
    assert ledger.current_balance(db_session) == 640.0
