"""Bridge Python<->JS per l'app desktop (PyWebView).

Ogni metodo pubblico di `Api` e' invocabile dal frontend via
`window.pywebview.api.<metodo>(...)` e ritorna dati JSON-serializzabili letti
dal backend reale. Nessuna logica di dominio nuova qui: si orchestra soltanto
(reporting, profiles, budget, planning, reference_sync). Ogni metodo apre e
chiude la propria sessione DB.

I metodi sono progettati per non sollevare eccezioni verso JS: ritornano
`{"ok": True, ...}` oppure `{"ok": False, "error": "..."}`, cosi' il
frontend puo' mostrare messaggi puliti.
"""

from __future__ import annotations

import datetime as dt
import functools
import logging
import subprocess
from pathlib import Path

from .. import backlog, config, reporting
from ..budget import estimate as budget_estimate
from ..budget import ledger as budget_ledger
from ..budget import sync as budget_sync
from ..budget.errors import BudgetInsufficientError
from ..db.base import SessionLocal, init_db
from ..db.models import ContentPiece, PlanWeek, Profile, ReferenceItem
from ..planning import plan as planning
from ..planning.quota import GIORNI_VALIDI
from ..production import engine as production_engine
from ..production import pipeline_spec
from ..profiles import manager as profiles
from ..reference_sync import allocator, sync as reference_sync

logger = logging.getLogger(__name__)

CONTENT_TYPES = ["video_talking", "video_balletti", "video_caption", "carosello", "stories"]


def _endpoint(fn):
    """Wrappa un metodo API: apre sessione, committa, cattura eccezioni in {ok:False}."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            with SessionLocal() as session:
                result = fn(self, session, *args, **kwargs)
                session.commit()
                if isinstance(result, dict) and "ok" not in result:
                    result = {"ok": True, **result}
                return result
        except BudgetInsufficientError as exc:
            return {"ok": False, "error": str(exc), "kind": "budget", "needed": exc.needed, "available": exc.available}
        except Exception as exc:  # noqa: BLE001 — il frontend deve ricevere sempre una risposta
            logger.exception("Errore in endpoint %s", fn.__name__)
            return {"ok": False, "error": str(exc)}

    return wrapper


def _profile_dict(p: Profile) -> dict:
    return {"id": p.id, "nome": p.nome, "tipo_contenuto": p.tipo_contenuto, "attivo": p.attivo, "creator_id": p.creator_id}


def _plan_grid(session, plan: PlanWeek) -> dict:
    pieces = session.query(ContentPiece).filter(ContentPiece.plan_week_id == plan.id).all()
    grid = {ct: {g: 0 for g in GIORNI_VALIDI} for ct in CONTENT_TYPES}
    for p in pieces:
        if p.content_type in grid and p.scheduled_day in grid[p.content_type]:
            grid[p.content_type][p.scheduled_day] += 1
    totals_by_type = {ct: sum(grid[ct].values()) for ct in CONTENT_TYPES}
    totals_by_day = {g: sum(grid[ct][g] for ct in CONTENT_TYPES) for g in GIORNI_VALIDI}
    assigned = sum(1 for p in pieces if p.reference_id is not None)
    return {
        "id": plan.id,
        "profile_id": plan.profile_id,
        "week_start": plan.week_start.isoformat(),
        "week_end": plan.week_end.isoformat(),
        "status": plan.status,
        "version": plan.version,
        "grid": grid,
        "totals_by_type": totals_by_type,
        "totals_by_day": totals_by_day,
        "total": sum(totals_by_type.values()),
        "assigned_references": assigned,
        "missing_references": max(0, len(pieces) - assigned),
    }


def _today_agenda(session) -> dict:
    """Cosa e' pianificato per OGGI (giorno della settimana corrente) per il
    profilo attivo. `scheduled_day` su ContentPiece e' un giorno della
    settimana ("lun".."dom"), non una data assoluta: per sapere cosa
    corrisponde a "oggi" serve trovare il PlanWeek la cui settimana
    (week_start..week_end) contiene la data odierna, poi filtrare i pezzi
    per il giorno corrispondente. GIORNI_VALIDI e' ordinato lun..dom, stesso
    ordine di `date.weekday()` (0=lun), quindi l'indice combacia
    direttamente senza bisogno di una mappa a parte."""
    today = dt.date.today()
    giorno = GIORNI_VALIDI[today.weekday()]
    active = profiles.get_active_profile(session)
    if active is None:
        return {"has_profile": False, "giorno": giorno, "plan": None, "pieces": []}

    plan = (
        session.query(PlanWeek)
        .filter(PlanWeek.profile_id == active.id, PlanWeek.week_start <= today, PlanWeek.week_end >= today)
        .first()
    )
    if plan is None:
        return {"has_profile": True, "profile_nome": active.nome, "giorno": giorno, "plan": None, "pieces": []}

    pieces = (
        session.query(ContentPiece)
        .filter(ContentPiece.plan_week_id == plan.id, ContentPiece.scheduled_day == giorno)
        .order_by(ContentPiece.content_type)
        .all()
    )
    return {
        "has_profile": True,
        "profile_nome": active.nome,
        "giorno": giorno,
        "plan": {"id": plan.id, "status": plan.status},
        "pieces": [
            {
                "id": p.id,
                "content_type": p.content_type,
                "status": p.status,
                "has_reference": p.reference_id is not None,
            }
            for p in pieces
        ],
    }


def _list_references(session, *, status=None, category=None, limit=50) -> list:
    """Lista filtrabile di reference per la Libreria (a differenza di
    `_reference_stats`, che ritorna solo aggregati + le 10 piu' recenti).
    Stesso criterio di ordinamento di `_reference_stats` (piu' recenti
    prima, per coerenza)."""
    q = session.query(ReferenceItem)
    if status:
        q = q.filter(ReferenceItem.status == status)
    if category:
        q = q.filter(ReferenceItem.source_category == category)
    rows = sorted(q.all(), key=lambda r: r.downloaded_at or r.imported_at, reverse=True)[:limit]
    return [
        {
            "id": r.id,
            "url": r.source_url,
            "status": r.status,
            "source_tab": r.source_tab,
            "source_category": r.source_category,
            "week_start": r.week_start.isoformat() if r.week_start else None,
            "downloaded_at": r.downloaded_at.isoformat() if r.downloaded_at else None,
            "has_caption": bool(r.original_caption),
            "has_local_media": bool(r.local_video_path or r.frame_paths),
            "error_message": r.error_message,
        }
        for r in rows
    ]


def _reference_folder(item: ReferenceItem) -> Path | None:
    candidate = item.local_video_path or (item.frame_paths[0] if item.frame_paths else None)
    if not candidate:
        return None
    return Path(candidate).resolve().parent


def _reference_stats(session) -> dict:
    rows = session.query(ReferenceItem).all()
    by_status: dict = {}
    by_week: dict = {}
    by_category: dict = {}
    too_old = 0
    cutoff = dt.date.today() - dt.timedelta(days=config.REFERENCE_RETENTION_DAYS)
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        week_key = r.week_start.isoformat() if r.week_start else "senza settimana"
        by_week[week_key] = by_week.get(week_key, 0) + 1
        cat_key = f"{r.source_tab or '—'} / {r.source_category or '—'}"
        by_category[cat_key] = by_category.get(cat_key, 0) + 1
        if r.week_end and r.week_end < cutoff:
            too_old += 1
    latest = sorted(
        rows,
        key=lambda r: r.downloaded_at or r.imported_at,
        reverse=True,
    )[:10]
    error_total = sum(by_status.get(s, 0) for s in ("error", "download_error", "private", "unavailable", "transcription_error"))
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_week": dict(sorted(by_week.items(), reverse=True)),
        "by_category": dict(sorted(by_category.items())),
        "ready": by_status.get("ready", 0),
        "pending": by_status.get("pending", 0),
        "error": error_total,
        "too_old": too_old,
        "retention_days": config.REFERENCE_RETENTION_DAYS,
        "selection_weeks": config.REFERENCE_SELECTION_WEEKS,
        "latest": [
            {
                "id": r.id,
                "url": r.source_url,
                "status": r.status,
                "week_start": r.week_start.isoformat() if r.week_start else None,
                "source_tab": r.source_tab,
                "source_category": r.source_category,
                "downloaded_at": r.downloaded_at.isoformat() if r.downloaded_at else None,
                "has_caption": bool(r.original_caption),
            }
            for r in latest
        ],
    }


def _production_preview(session, plan_id=None) -> dict:
    q = (
        session.query(ContentPiece)
        .join(PlanWeek, ContentPiece.plan_week_id == PlanWeek.id)
        .filter(
            ContentPiece.status == "reference_ready",
            ContentPiece.reference_id.isnot(None),
            PlanWeek.status == "approvato",
        )
    )
    if plan_id is not None:
        q = q.filter(ContentPiece.plan_week_id == int(plan_id))
    pieces = q.all()
    cache: dict = {}
    stima = 0.0
    by_type: dict = {}
    for p in pieces:
        c = budget_estimate.estimate_content_type(p.content_type, cache=cache)
        stima += c
        by_type[p.content_type] = by_type.get(p.content_type, 0) + 1
    balance = budget_ledger.current_balance(session)
    return {
        "ready_count": len(pieces),
        "by_type": by_type,
        "estimated_cost": stima,
        "balance": balance,
        "covers": balance >= stima,
    }


class Api:
    # --- dashboard / sistema ---

    @_endpoint
    def overview(self, session):
        return {"overview": reporting.overview(session)}

    @_endpoint
    def today_agenda(self, session):
        return _today_agenda(session)

    # --- profili (Creator) ---

    @_endpoint
    def list_profiles(self, session):
        active = profiles.get_active_profile(session)
        creators = {c.id: c.nome for c in profiles.list_creators(session)}
        rows = []
        for p in profiles.list_profiles(session):
            d = _profile_dict(p)
            d["creator"] = creators.get(p.creator_id)
            d["is_active"] = active is not None and p.id == active.id
            rows.append(d)
        return {"profiles": rows, "creators": [{"id": cid, "nome": nome} for cid, nome in creators.items()]}

    @_endpoint
    def create_creator(self, session, nome):
        c = profiles.create_creator(session, nome)
        return {"creator": {"id": c.id, "nome": c.nome}}

    @_endpoint
    def create_profile(self, session, creator_id, nome, tipo):
        p = profiles.create_profile(session, creator_id=int(creator_id), nome=nome, tipo_contenuto=tipo)
        return {"profile": _profile_dict(p)}

    @_endpoint
    def set_active_profile(self, session, profile_id):
        p = profiles.set_active_profile(session, int(profile_id))
        return {"profile": _profile_dict(p)}

    @_endpoint
    def delete_profile(self, session, profile_id, force=False):
        profiles.delete_profile(session, int(profile_id), force=bool(force))
        return {}

    # --- budget (Costi) ---

    @_endpoint
    def budget_status(self, session, plan_id=None):
        balance = budget_ledger.current_balance(session)
        data = {"balance": balance}
        if plan_id is not None:
            plan = session.get(PlanWeek, int(plan_id))
            if plan is not None:
                estimated = budget_estimate.estimate_plan(session, plan, persist=False)
                data.update({
                    "plan_cost": estimated,
                    "coverage": balance - estimated,
                    "covers": balance >= estimated,
                })
        return data

    @_endpoint
    def budget_topup(self, session, credits, motivo="ricarica"):
        budget_ledger.record_topup(session, credits=float(credits), motivo=motivo)
        return {"balance": budget_ledger.current_balance(session)}

    @_endpoint
    def budget_sync(self, session):
        result = budget_sync.sync_from_higgsfield(session)
        result["balance"] = budget_ledger.current_balance(session)
        return result

    # --- referenze (Libreria) ---

    @_endpoint
    def reference_stats(self, session):
        return _reference_stats(session)

    @_endpoint
    def references_sync(self, session, limit=None, tab=None, category=None):
        result = reference_sync.run_once(
            max_items=(int(limit) if limit is not None else None),
            source_tab=tab,
            source_category=category,
        )
        stats = _reference_stats(session)
        stats["sync"] = result
        return stats

    @_endpoint
    def references_sync_policy(self, session, policy=None):
        result = reference_sync.run_policy_once(policy=policy)
        stats = _reference_stats(session)
        stats["sync"] = result
        return stats

    @_endpoint
    def list_references(self, session, status=None, category=None, limit=50):
        return {"references": _list_references(session, status=status, category=category, limit=int(limit))}

    @_endpoint
    def retry_reference(self, session, reference_id):
        result = reference_sync.retry_reference(int(reference_id))
        return {"retry": result}

    @_endpoint
    def open_reference_folder(self, session, reference_id):
        item = session.get(ReferenceItem, int(reference_id))
        if item is None:
            return {"ok": False, "error": f"Reference {reference_id} inesistente"}
        folder = _reference_folder(item)
        if folder is None or not folder.is_dir():
            return {"ok": False, "error": "Nessuna cartella locale per questa reference"}
        media_root = config.MEDIA_DIR.resolve()
        if folder != media_root and media_root not in folder.parents:
            return {"ok": False, "error": "Percorso fuori dalla cartella media, rifiutato per sicurezza"}
        subprocess.run(["open", str(folder)], check=False)
        return {"folder": str(folder)}

    # --- piano (Piano) ---

    @_endpoint
    def list_plans(self, session, profile_id=None):
        q = session.query(PlanWeek)
        if profile_id is not None:
            q = q.filter(PlanWeek.profile_id == int(profile_id))
        plans = q.order_by(PlanWeek.week_start.desc()).all()
        return {"plans": [
            {"id": p.id, "profile_id": p.profile_id, "week_start": p.week_start.isoformat(),
             "week_end": p.week_end.isoformat(), "status": p.status, "version": p.version}
            for p in plans
        ]}

    @_endpoint
    def create_plan(self, session, profile_id, week_start, week_end):
        plan = planning.create_plan_week(
            session,
            profile_id=int(profile_id),
            week_start=dt.date.fromisoformat(week_start),
            week_end=dt.date.fromisoformat(week_end),
        )
        return {"plan": _plan_grid(session, plan)}

    @_endpoint
    def get_plan(self, session, plan_id):
        plan = session.get(PlanWeek, int(plan_id))
        if plan is None:
            return {"ok": False, "error": f"Piano {plan_id} inesistente"}
        return {"plan": _plan_grid(session, plan)}

    @_endpoint
    def plan_set_cell(self, session, plan_id, content_type, giorno, target):
        plan = session.get(PlanWeek, int(plan_id))
        if plan is None:
            return {"ok": False, "error": f"Piano {plan_id} inesistente"}
        planning.set_cell_count(session, plan, content_type=content_type, scheduled_day=giorno, target=int(target))
        return {"plan": _plan_grid(session, plan)}

    @_endpoint
    def approve_plan(self, session, plan_id):
        plan = session.get(PlanWeek, int(plan_id))
        if plan is None:
            return {"ok": False, "error": f"Piano {plan_id} inesistente"}
        estimated = planning.approve_plan(session, plan)
        assignment = allocator.assign_references_to_plan(session, plan.id)
        return {
            "plan": _plan_grid(session, plan),
            "estimated": estimated,
            "balance": budget_ledger.current_balance(session),
            "reference_assignment": {
                "assigned": assignment.assigned,
                "missing": assignment.missing,
                "by_content_type": assignment.by_content_type,
            },
        }

    @_endpoint
    def assign_plan_references(self, session, plan_id):
        plan = session.get(PlanWeek, int(plan_id))
        if plan is None:
            return {"ok": False, "error": f"Piano {plan_id} inesistente"}
        result = allocator.assign_references_to_plan(session, plan.id)
        return {
            "plan": _plan_grid(session, plan),
            "assigned": result.assigned,
            "missing": result.missing,
            "by_content_type": result.by_content_type,
        }

    # --- produzione (Produzione) ---

    @_endpoint
    def production_preview(self, session, plan_id=None):
        """Anteprima SENZA COSTI di cosa verrebbe prodotto (pezzi pronti di piani
        approvati) e stima crediti. Non genera nulla, non spende (equivalente
        di 'Avvia una prova senza costi' degli screenshot)."""
        return _production_preview(session, plan_id=plan_id)

    @_endpoint
    def production_run(self, session, plan_id=None, confirmation=None):
        if confirmation != "PRODUCI":
            return {"ok": False, "error": "Conferma richiesta: scrivi/manda PRODUCI per avviare la produzione reale."}
        q = session.query(PlanWeek).filter(PlanWeek.status == "approvato")
        if plan_id is not None:
            q = q.filter(PlanWeek.id == int(plan_id))
        for plan in q.all():
            allocator.assign_references_to_plan(session, plan.id)
        session.flush()
        preview = _production_preview(session, plan_id=plan_id)
        if preview["ready_count"] <= 0:
            return {"ok": False, "error": "Nessun contenuto pronto da produrre."}
        if not preview["covers"]:
            return {"ok": False, "error": "Budget insufficiente per avviare la produzione reale.", **preview}
        result = production_engine.run_once(session, plan_id=(int(plan_id) if plan_id is not None else None))
        return {"preview_before": preview, "production": result}

    # --- backlog (Da migliorare) ---

    @_endpoint
    def list_backlog(self, session, status="aperto"):
        status_filter = None if status == "tutti" else status
        notes = backlog.list_notes(session, status=status_filter)
        return {"notes": [
            {
                "id": n.id, "created_at": n.created_at.isoformat(), "category": n.category,
                "title": n.title, "description": n.description, "status": n.status,
            }
            for n in notes
        ]}

    @_endpoint
    def add_backlog_note(self, session, category, title, description=""):
        note = backlog.add_note(session, category=category, title=title, description=description)
        return {"note": {"id": note.id}}

    @_endpoint
    def set_backlog_status(self, session, note_id, status):
        backlog.set_status(session, int(note_id), status)
        return {}

    # --- costanti utili al frontend ---

    @_endpoint
    def meta(self, session):
        return {
            "content_types": CONTENT_TYPES,
            "giorni": list(GIORNI_VALIDI),
            "tipi_profilo": list(profiles.TIPI_CONTENUTO_VALIDI),
            "pipeline": {ct: [op.stage for op in pipeline_spec.generation_ops(ct)] for ct in CONTENT_TYPES},
        }


def get_api() -> Api:
    init_db()
    return Api()
