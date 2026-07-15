"""Planning: gestione dei PlanWeek — creazione, aggiunta/rimozione pezzi con
quote, versioning, workflow bozza -> approvato.

Punto di integrazione con Step 3 (Budget): l'approvazione di un piano stima
il costo (budget.estimate) e lo confronta col saldo (budget.ledger); se il
saldo non copre, l'approvazione e' BLOCCATA — replica la logica "budget non
copre il piano" degli screenshot.

Versioning: ogni modifica al contenuto del piano incrementa `version`. Una
modifica a un piano gia' 'approvato' lo riporta a 'bozza' (decisione presa
in build, vedi docs/ai-craft-architecture.md §9): un piano approvato e gia'
coperto a budget non deve guadagnare silenziosamente pezzi senza un nuovo
controllo di budget.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..budget import estimate as budget_estimate
from ..budget import ledger as budget_ledger
from ..budget.errors import BudgetInsufficientError
from ..db.models import ContentPiece, PlanWeek
from . import quota as quota_mod


def create_plan_week(session: Session, *, profile_id: int, week_start: dt.date, week_end: dt.date) -> PlanWeek:
    plan = PlanWeek(profile_id=profile_id, week_start=week_start, week_end=week_end, status="bozza", version=1)
    session.add(plan)
    session.flush()
    return plan


def _touch(plan: PlanWeek) -> None:
    """Registra una modifica al piano: +1 version e, se era approvato, torna in bozza."""
    plan.version += 1
    if plan.status == "approvato":
        plan.status = "bozza"


def _pezzi_del_piano(session: Session, plan: PlanWeek) -> list:
    return session.scalars(select(ContentPiece).where(ContentPiece.plan_week_id == plan.id)).all()


def add_content_piece(
    session: Session,
    plan: PlanWeek,
    *,
    content_type: str,
    scheduled_day: Optional[str] = None,
    reference_id: Optional[int] = None,
    requested_source_category: Optional[str] = None,
    policy: Optional[quota_mod.QuotaPolicy] = None,
) -> ContentPiece:
    if scheduled_day is not None and scheduled_day not in quota_mod.GIORNI_VALIDI:
        raise ValueError(f"scheduled_day non valido: {scheduled_day!r} (attesi {quota_mod.GIORNI_VALIDI})")

    if policy is not None:
        esistenti = [(p.content_type, p.scheduled_day) for p in _pezzi_del_piano(session, plan)]
        policy.check(content_type=content_type, scheduled_day=scheduled_day, pezzi_esistenti=esistenti)

    piece = ContentPiece(
        profile_id=plan.profile_id,
        reference_id=reference_id,
        content_type=content_type,
        plan_week_id=plan.id,
        scheduled_day=scheduled_day,
        requested_source_category=requested_source_category,
        status="reference_ready",
    )
    session.add(piece)
    _touch(plan)
    session.flush()
    return piece


def remove_content_piece(session: Session, plan: PlanWeek, piece: ContentPiece) -> None:
    if piece.plan_week_id != plan.id:
        raise ValueError("Il ContentPiece non appartiene a questo piano")
    session.delete(piece)
    _touch(plan)
    session.flush()


def reschedule_content_piece(session: Session, plan: PlanWeek, piece: ContentPiece, *, scheduled_day: str) -> None:
    if scheduled_day not in quota_mod.GIORNI_VALIDI:
        raise ValueError(f"scheduled_day non valido: {scheduled_day!r}")
    if piece.plan_week_id != plan.id:
        raise ValueError("Il ContentPiece non appartiene a questo piano")
    piece.scheduled_day = scheduled_day
    _touch(plan)
    session.flush()


def set_cell_count(
    session: Session,
    plan: PlanWeek,
    *,
    content_type: str,
    scheduled_day: str,
    target: int,
    policy: Optional[quota_mod.QuotaPolicy] = None,
) -> int:
    """Porta il numero di pezzi (content_type, giorno) al valore `target`,
    aggiungendo o rimuovendo pezzi — e' la logica dietro gli stepper +/- del
    calendario editoriale. Ritorna il conteggio finale.

    Rimuove per ultimi i pezzi piu' recenti; non tocca pezzi gia' oltre lo
    stato 'reference_ready' (in produzione/consegnati) quando deve ridurre,
    per non cancellare lavoro gia' avviato.
    """
    if scheduled_day not in quota_mod.GIORNI_VALIDI:
        raise ValueError(f"scheduled_day non valido: {scheduled_day!r}")
    if target < 0:
        raise ValueError("target non puo' essere negativo")

    esistenti = [
        p for p in _pezzi_del_piano(session, plan)
        if p.content_type == content_type and p.scheduled_day == scheduled_day
    ]
    attuale = len(esistenti)

    if target > attuale:
        for _ in range(target - attuale):
            add_content_piece(
                session, plan, content_type=content_type, scheduled_day=scheduled_day, policy=policy
            )
    elif target < attuale:
        rimovibili = [p for p in esistenti if p.status == "reference_ready"]
        da_rimuovere = attuale - target
        for piece in sorted(rimovibili, key=lambda p: p.id, reverse=True)[:da_rimuovere]:
            remove_content_piece(session, plan, piece)

    # riconteggio reale (potrebbe non aver raggiunto il target se alcuni pezzi
    # sono gia' in produzione e non rimovibili)
    return sum(
        1 for p in _pezzi_del_piano(session, plan)
        if p.content_type == content_type and p.scheduled_day == scheduled_day
    )


def approve_plan(session: Session, plan: PlanWeek, *, cost_fn=budget_estimate.default_cost_fn) -> float:
    """Approva il piano se il saldo copre il costo stimato. Ritorna la stima.

    Blocca con BudgetInsufficientError se saldo < stima. La stima viene
    salvata su ogni ContentPiece.cost_credits_estimated.
    """
    estimated = budget_estimate.estimate_plan(session, plan, cost_fn=cost_fn, persist=True)
    balance = budget_ledger.current_balance(session)
    if estimated > balance:
        raise BudgetInsufficientError(needed=estimated, available=balance)

    plan.status = "approvato"
    plan.version += 1
    session.flush()
    return estimated


def duplicate_plan_week(session: Session, source_plan: PlanWeek, *, week_start: dt.date, week_end: dt.date) -> PlanWeek:
    """Duplica la griglia (content_type x giorno) di un piano su una nuova
    settimana per lo stesso profilo — richiesto dall'utente per non
    ridigitare a mano una settimana simile alla precedente. Copia solo i
    CONTEGGI, non le reference assegnate (la nuova settimana pesca reference
    fresche dalla Libreria quando viene approvata) ne' stato/costi. Il nuovo
    piano nasce sempre in bozza, come uno creato da zero."""
    new_plan = create_plan_week(session, profile_id=source_plan.profile_id, week_start=week_start, week_end=week_end)

    counts: dict = {}
    for piece in _pezzi_del_piano(session, source_plan):
        if piece.scheduled_day is None:
            continue
        key = (piece.content_type, piece.scheduled_day)
        counts[key] = counts.get(key, 0) + 1

    for (content_type, giorno), n in counts.items():
        set_cell_count(session, new_plan, content_type=content_type, scheduled_day=giorno, target=n)

    return new_plan
