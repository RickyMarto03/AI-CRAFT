"""Sincronizza il saldo reale di Higgsfield nel CreditLedger interno.

Il CreditLedger resta l'unica fonte di verita' interna (regola ferma), ma
per essere significativo deve partire allineato ai crediti realmente
disponibili su Higgsfield. Questa funzione legge il saldo reale
(`higgsfield account status`) e, se diverge dal saldo interno, registra UNA
voce di rettifica che riporta l'interno sul reale — invece di sovrascrivere
il ledger, cosi' la storia dei movimenti resta tracciata.

`account_status_fn` iniettabile per i test (nessuna dipendenza da rete).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..production import higgsfield_client
from . import ledger

_EPS = 1e-9


def sync_from_higgsfield(session: Session, *, account_status_fn=higgsfield_client.account_status) -> dict:
    real = float(account_status_fn().get("credits", 0.0))
    internal = ledger.current_balance(session)
    adjustment = real - internal

    if abs(adjustment) > _EPS:
        ledger.record(session, delta_credits=adjustment, motivo="sync saldo Higgsfield")

    return {"real": real, "internal_before": internal, "adjustment": adjustment}
