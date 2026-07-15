"""Stima del costo in crediti di un ContentPiece o di un intero PlanWeek,
PRIMA di produrre. Serve al workflow di approvazione piano (Planning) per
bloccare l'approvazione se il saldo non copre — replica la logica "budget
non copre il piano" degli screenshot.

Il costo Higgsfield dipende da modello + parametri (risoluzione, durata),
non dal contenuto del prompt: la stima usa un prompt segnaposto e mette in
cache il risultato per ogni combinazione (job_type, params) distinta, cosi'
stimare un piano di N pezzi non fa N chiamate ridondanti.

`cost_fn` e' iniettabile: di default interroga Higgsfield reale, ma i test
passano una funzione finta per non dipendere da rete/credenziali.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ContentPiece, PlanWeek
from ..production import higgsfield_client, pipeline_spec


def default_cost_fn(job_type: str, params: dict) -> float:
    """Interroga `higgsfield generate cost` per (modello, params). Costo reale."""
    cost = higgsfield_client.estimate_cost(job_type, prompt="stima", **params)
    return float(cost or 0.0)


def estimate_content_type(content_type: str, *, cost_fn=default_cost_fn, cache: dict | None = None) -> float:
    """Costo stimato di un singolo ContentPiece di questo tipo (somma degli op di generazione).

    Se un GenerationOp ha `manual_cost_estimate` impostato, si usa quello
    invece di interrogare Higgsfield: alcuni job_type (es.
    kling3_0_motion_control) non sono stimabili via API senza file reali
    gia' caricati, che in fase di stima piano non esistono ancora — vedi
    pipeline_spec.py e docs §12.2.
    """
    cache = cache if cache is not None else {}
    total = 0.0
    for op in pipeline_spec.generation_ops(content_type):
        if op.manual_cost_estimate is not None:
            total += op.count * op.manual_cost_estimate
            continue
        key = op.params_key()
        if key not in cache:
            cache[key] = cost_fn(op.job_type, op.params)
        total += op.count * cache[key]
    return total


def estimate_plan(session: Session, plan_week: PlanWeek, *, cost_fn=default_cost_fn, persist: bool = True) -> float:
    """Costo stimato dell'intero piano. Se persist=True, salva la stima su
    ogni ContentPiece.cost_credits_estimated e ritorna la somma."""
    pieces = session.scalars(
        select(ContentPiece).where(ContentPiece.plan_week_id == plan_week.id)
    ).all()

    cache: dict = {}
    total = 0.0
    for piece in pieces:
        piece_cost = estimate_content_type(piece.content_type, cost_fn=cost_fn, cache=cache)
        if persist:
            piece.cost_credits_estimated = piece_cost
        total += piece_cost
    return total
