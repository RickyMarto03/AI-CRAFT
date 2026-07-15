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

import calendar
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
from ..db.models import ContentPiece, ContentPieceEvent, CreditLedger, PlanWeek, Profile, ReferenceItem
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
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
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


def _list_references(session, *, status=None, category=None, search=None, limit=50, offset=0) -> dict:
    """Lista filtrabile/cercabile/paginata di reference per la Libreria (a
    differenza di `_reference_stats`, che ritorna solo aggregati + le 10
    piu' recenti). Stesso criterio di ordinamento di `_reference_stats`
    (piu' recenti prima, per coerenza). `search` cerca per sottostringa
    (case-insensitive) in caption originale o URL — filtro in Python, non
    SQL LIKE: la libreria ha qualche migliaio di righe al massimo, non
    serve un indice full-text per questa scala."""
    q = session.query(ReferenceItem)
    if status:
        q = q.filter(ReferenceItem.status == status)
    if category:
        q = q.filter(ReferenceItem.source_category == category)
    rows = q.all()
    if search:
        needle = search.strip().lower()
        rows = [r for r in rows if needle in (r.original_caption or "").lower() or needle in r.source_url.lower()]
    rows = sorted(rows, key=lambda r: r.downloaded_at or r.imported_at, reverse=True)
    total = len(rows)
    page = rows[offset:offset + limit]
    items = []
    for r in page:
        thumb = _reference_thumbnail(r)
        items.append({
            "id": r.id,
            "url": r.source_url,
            "status": r.status,
            "source_tab": r.source_tab,
            "source_category": r.source_category,
            "week_start": r.week_start.isoformat() if r.week_start else None,
            "downloaded_at": r.downloaded_at.isoformat() if r.downloaded_at else None,
            "original_caption": r.original_caption,
            "has_transcript": bool(r.transcript),
            "content_type_hint": r.content_type_hint,
            "has_local_media": bool(r.local_video_path or r.frame_paths),
            "thumbnail_url": f"file://{thumb}" if thumb else None,
            "error_message": r.error_message,
            "download_attempts": r.download_attempts or 0,
            "max_download_attempts": reference_sync.MAX_DOWNLOAD_ATTEMPTS,
            "retryable": r.status in _ERROR_STATUSES and (r.download_attempts or 0) < reference_sync.MAX_DOWNLOAD_ATTEMPTS,
        })
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def _reference_folder(item: ReferenceItem) -> Path | None:
    candidate = item.local_video_path or (item.frame_paths[0] if item.frame_paths else None)
    if not candidate:
        return None
    return Path(candidate).resolve().parent


def _reference_thumbnail(item: ReferenceItem) -> Path | None:
    """Immagine rappresentativa della reference per la UI: la prima foto
    per i caroselli (gia' un file locale reale, zero costo aggiuntivo), un
    frame estratto e messo in cache su disco per i video — un singolo frame
    via ffmpeg, non il rilevatore DNN di frame_picker (qui serve solo
    un'anteprima visiva, non trovare il personaggio). Cache permanente
    accanto al video: generata una volta sola, i caricamenti successivi
    della Libreria sono istantanei."""
    if item.frame_paths:
        p = Path(item.frame_paths[0])
        return p if p.exists() else None
    if not item.local_video_path:
        return None
    video_path = Path(item.local_video_path)
    if not video_path.exists():
        return None
    thumb_path = video_path.with_name(video_path.stem + "_thumb.jpg")
    if thumb_path.exists():
        return thumb_path
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.5", "-i", str(video_path), "-frames:v", "1", "-vf", "scale=240:-1", str(thumb_path)],
            capture_output=True, check=True, timeout=15,
        )
    except Exception:
        return None
    return thumb_path if thumb_path.exists() else None


def _piece_thumbnail(piece: ContentPiece) -> Path | None:
    """Prima immagine tra gli asset generati di un pezzo (per talking/
    balletti e' la foto Ruby2, per carosello/stories la prima foto) — sono
    gia' file locali reali (vedi engine._localize_asset), nessuna
    generazione aggiuntiva serve qui."""
    for asset in piece.generated_assets or []:
        p = Path(asset)
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and p.exists():
            return p
    return None


_ERROR_STATUSES = reference_sync.ERROR_STATUSES  # unica fonte di verita' in reference_sync.sync


def _reference_weekly_trend(session, *, weeks: int = 8) -> list:
    """Andamento settimanale della Libreria (totale/pronte/errore/in attesa
    per settimana dello sheet), per vedere se il ritmo di acquisizione e il
    tasso di successo cambiano nel tempo — non solo un totale statico."""
    rows = session.query(ReferenceItem).filter(ReferenceItem.week_start.is_not(None)).all()
    by_week: dict = {}
    for r in rows:
        key = r.week_start.isoformat()
        bucket = by_week.setdefault(key, {"week_start": key, "total": 0, "ready": 0, "error": 0, "pending": 0})
        bucket["total"] += 1
        if r.status == "ready":
            bucket["ready"] += 1
        elif r.status in _ERROR_STATUSES:
            bucket["error"] += 1
        elif r.status == "pending":
            bucket["pending"] += 1
    ordered = sorted(by_week.values(), key=lambda b: b["week_start"], reverse=True)[:weeks]
    return list(reversed(ordered))  # cronologico, piu' vecchia prima


def _content_type_by_piece_id(session, piece_ids: set) -> dict:
    if not piece_ids:
        return {}
    rows = session.query(ContentPiece.id, ContentPiece.content_type).filter(ContentPiece.id.in_(piece_ids)).all()
    return {pid: ct for pid, ct in rows}


def _ledger_history(session, *, limit: int = 50) -> list:
    rows = session.query(CreditLedger).order_by(CreditLedger.id.desc()).limit(limit).all()
    piece_ids = {r.content_piece_id for r in rows if r.content_piece_id is not None}
    content_types = _content_type_by_piece_id(session, piece_ids)
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "delta_credits": r.delta_credits,
            "motivo": r.motivo,
            "content_piece_id": r.content_piece_id,
            "content_type": content_types.get(r.content_piece_id),
        }
        for r in rows
    ]


def _spend_by_content_type(session) -> dict:
    rows = (
        session.query(CreditLedger)
        .filter(CreditLedger.delta_credits < 0, CreditLedger.content_piece_id.is_not(None))
        .all()
    )
    piece_ids = {r.content_piece_id for r in rows}
    content_types = _content_type_by_piece_id(session, piece_ids)
    totals: dict = {}
    for r in rows:
        ct = content_types.get(r.content_piece_id)
        if ct is None:
            continue
        totals[ct] = totals.get(ct, 0.0) + abs(r.delta_credits)
    return totals


def _monthly_projection(session, *, window_days: int = 14) -> dict:
    """Proiezione di consumo mensile: spesa media giornaliera nella finestra
    recente, estrapolata su 30 giorni. Una stima grezza (nessuna
    stagionalita'), utile solo per farsi un'idea del ritmo attuale."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=window_days)
    rows = (
        session.query(CreditLedger)
        .filter(CreditLedger.delta_credits < 0, CreditLedger.timestamp >= cutoff)
        .all()
    )
    spent = sum(abs(r.delta_credits) for r in rows)
    daily_avg = spent / window_days if window_days else 0.0
    return {
        "window_days": window_days,
        "spent_in_window": spent,
        "daily_avg": daily_avg,
        "projected_30_days": daily_avg * 30,
    }


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
    error_total = sum(by_status.get(s, 0) for s in _ERROR_STATUSES)
    error_retryable = sum(
        1 for r in rows
        if r.status in _ERROR_STATUSES and (r.download_attempts or 0) < reference_sync.MAX_DOWNLOAD_ATTEMPTS
    )
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_week": dict(sorted(by_week.items(), reverse=True)),
        "by_category": dict(sorted(by_category.items())),
        "ready": by_status.get("ready", 0),
        "pending": by_status.get("pending", 0),
        "error": error_total,
        "error_retryable": error_retryable,
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
            pieces = session.query(ContentPiece).filter(ContentPiece.profile_id == p.id).all()
            d["content_stats"] = {
                "total": len(pieces),
                "delivered": sum(1 for x in pieces if x.status == "delivered"),
                "cost_actual": sum(x.cost_credits_actual or 0.0 for x in pieces),
            }
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

    @_endpoint
    def ledger_history(self, session, limit=50):
        return {"entries": _ledger_history(session, limit=int(limit))}

    @_endpoint
    def spend_by_content_type(self, session):
        return {"totals": _spend_by_content_type(session)}

    @_endpoint
    def monthly_projection(self, session, window_days=14):
        return _monthly_projection(session, window_days=int(window_days))

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
    def list_references(self, session, status=None, category=None, search=None, limit=50, offset=0):
        result = _list_references(session, status=status, category=category, search=search, limit=int(limit), offset=int(offset))
        return {"references": result["items"], "total": result["total"], "offset": result["offset"], "limit": result["limit"]}

    @_endpoint
    def reference_weekly_trend(self, session, weeks=8):
        return {"weeks": _reference_weekly_trend(session, weeks=int(weeks))}

    @_endpoint
    def retry_reference(self, session, reference_id):
        result = reference_sync.retry_reference(int(reference_id))
        return {"retry": result}

    @_endpoint
    def retry_all_references(self, session, category=None):
        # _ERROR_STATUSES, non sync.RETRYABLE_STATUSES: deve ritentare
        # esattamente le reference che in Libreria mostrano gia' il bottone
        # "Riprova" singolo (falliti), non anche i "pending"/"downloading"
        # ancora mai processati — quelli li gestisce il sync normale.
        # Esclude anche chi ha gia' esaurito MAX_DOWNLOAD_ATTEMPTS (stessa
        # regola del bottone singolo, vedi "retryable" in _list_references).
        q = session.query(ReferenceItem).filter(
            ReferenceItem.status.in_(_ERROR_STATUSES),
            ReferenceItem.download_attempts < reference_sync.MAX_DOWNLOAD_ATTEMPTS,
        )
        if category:
            q = q.filter(ReferenceItem.source_category == category)
        ids = [r.id for r in q.all()]
        result = reference_sync.retry_all(ids)
        return {"retry_all": result}

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

    @_endpoint
    def duplicate_plan(self, session, plan_id, week_start, week_end):
        source = session.get(PlanWeek, int(plan_id))
        if source is None:
            return {"ok": False, "error": f"Piano {plan_id} inesistente"}
        new_plan = planning.duplicate_plan_week(
            session, source,
            week_start=dt.date.fromisoformat(week_start), week_end=dt.date.fromisoformat(week_end),
        )
        return {"plan": _plan_grid(session, new_plan)}

    @_endpoint
    def monthly_summary(self, session, profile_id, year, month):
        profile_id, year, month = int(profile_id), int(year), int(month)
        month_start = dt.date(year, month, 1)
        month_end = dt.date(year, month, calendar.monthrange(year, month)[1])
        plans = (
            session.query(PlanWeek)
            .filter(
                PlanWeek.profile_id == profile_id,
                PlanWeek.week_start <= month_end,
                PlanWeek.week_end >= month_start,
            )
            .order_by(PlanWeek.week_start)
            .all()
        )
        weeks = []
        totals_by_type: dict = {}
        total_pieces = 0
        for plan in plans:
            pieces = session.query(ContentPiece).filter(ContentPiece.plan_week_id == plan.id).all()
            by_type: dict = {}
            for p in pieces:
                by_type[p.content_type] = by_type.get(p.content_type, 0) + 1
                totals_by_type[p.content_type] = totals_by_type.get(p.content_type, 0) + 1
            total_pieces += len(pieces)
            weeks.append({
                "id": plan.id,
                "week_start": plan.week_start.isoformat(),
                "week_end": plan.week_end.isoformat(),
                "status": plan.status,
                "version": plan.version,
                "total": len(pieces),
                "by_type": by_type,
            })
        return {
            "year": year, "month": month,
            "weeks": weeks, "totals_by_type": totals_by_type, "total_pieces": total_pieces,
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

    @_endpoint
    def list_content_pieces(self, session, status=None, plan_id=None, limit=30):
        q = session.query(ContentPiece)
        if status:
            q = q.filter(ContentPiece.status == status)
        if plan_id is not None:
            q = q.filter(ContentPiece.plan_week_id == int(plan_id))
        pieces = q.order_by(ContentPiece.updated_at.desc()).limit(int(limit)).all()
        result = []
        for p in pieces:
            thumb = _piece_thumbnail(p)
            result.append({
                "id": p.id,
                "content_type": p.content_type,
                "status": p.status,
                "profile_nome": p.profile.nome if p.profile else None,
                "caption": p.caption,
                "cost_credits_actual": p.cost_credits_actual,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                "thumbnail_url": f"file://{thumb}" if thumb else None,
                "has_output": bool(p.generated_assets),
            })
        return {"pieces": result}

    @_endpoint
    def open_piece_folder(self, session, piece_id):
        piece = session.get(ContentPiece, int(piece_id))
        if piece is None:
            return {"ok": False, "error": f"ContentPiece {piece_id} inesistente"}
        asset = next((a for a in (piece.generated_assets or []) if Path(a).exists()), None)
        if asset is None:
            return {"ok": False, "error": "Nessun file locale per questo pezzo"}
        folder = Path(asset).resolve().parent
        allowed_roots = [config.DELIVERY_DIR.resolve(), config.WORK_DIR.resolve()]
        if not any(folder == root or root in folder.parents for root in allowed_roots):
            return {"ok": False, "error": "Percorso fuori dalle cartelle attese, rifiutato per sicurezza"}
        subprocess.run(["open", str(folder)], check=False)
        return {"folder": str(folder)}

    @_endpoint
    def piece_timeline(self, session, piece_id):
        piece = session.get(ContentPiece, int(piece_id))
        if piece is None:
            return {"ok": False, "error": f"ContentPiece {piece_id} inesistente"}
        events = (
            session.query(ContentPieceEvent)
            .filter(ContentPieceEvent.content_piece_id == piece.id)
            .order_by(ContentPieceEvent.id)
            .all()
        )
        return {
            "piece": {"id": piece.id, "content_type": piece.content_type, "status": piece.status},
            "events": [
                {
                    "stage": e.stage, "status": e.status,
                    "duration_seconds": e.duration_seconds, "detail": e.detail,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in events
            ],
        }

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
