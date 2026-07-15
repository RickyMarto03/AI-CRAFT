"""Unica fonte di verita' per il saldo crediti.

Regola ferma (CLAUDE.md): il saldo si legge SEMPRE da CreditLedger come somma
cumulativa dei delta, MAI calcolato/salvato ad-hoc altrove. Ogni scrittura
sul ledger passa da qui, cosi' consumo (delta negativo) e ricariche (delta
positivo) hanno un solo punto d'ingresso.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import CreditLedger


def current_balance(session: Session) -> float:
    """Saldo = somma di tutti i delta_credits nel ledger. Nessuna colonna 'saldo' salvata."""
    total = session.scalar(select(func.coalesce(func.sum(CreditLedger.delta_credits), 0.0)))
    return float(total or 0.0)


def record(session: Session, *, delta_credits: float, motivo: str, content_piece_id: int | None = None) -> CreditLedger:
    """Aggiunge una voce al ledger. delta negativo = consumo, positivo = ricarica.

    NON committa: lascia al chiamante il controllo della transazione, cosi' la
    voce di consumo puo' stare nella stessa transazione dell'aggiornamento del
    ContentPiece che l'ha generata.
    """
    entry = CreditLedger(delta_credits=delta_credits, motivo=motivo, content_piece_id=content_piece_id)
    session.add(entry)
    return entry


def record_consumption(session: Session, *, credits: float, motivo: str, content_piece_id: int | None = None) -> CreditLedger:
    """Registra un consumo. `credits` va passato positivo, viene salvato come delta negativo."""
    return record(session, delta_credits=-abs(credits), motivo=motivo, content_piece_id=content_piece_id)


def record_topup(session: Session, *, credits: float, motivo: str = "ricarica") -> CreditLedger:
    """Registra una ricarica di crediti (delta positivo)."""
    return record(session, delta_credits=abs(credits), motivo=motivo)
