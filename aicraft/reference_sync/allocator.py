"""Allocator reference: assegna automaticamente i contenuti IG scaricati.

La logica decisa con l'utente e' una libreria locale a coda rotante:
AI-CRAFT scarica i reference recenti dallo Sheet, li marca sullo Sheet come
acquisiti, poi la produzione pesca dal DB locale senza chiedere all'utente di
scegliere link singoli. Si usano al massimo le ultime N settimane disponibili
(default 2) e dentro quella finestra si consuma dal piu' vecchio al piu'
nuovo, cosi' il materiale fresco non viene bruciato subito.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db.models import ContentPiece, ReferenceItem


CONTENT_TYPE_CATEGORIES = {
    "video_talking": ("TALKING",),
    "video_balletti": ("BALLETTI/LIPSYNC",),
    "video_caption": ("CAPTION",),
    "carosello": ("BOOBS", "BOOTY", "GENERAL"),
    "stories": ("GENERAL",),
}


@dataclass
class AssignmentResult:
    assigned: int
    missing: int
    by_content_type: dict[str, int]


def categories_for(content_type: str, requested_category: Optional[str] = None) -> tuple[str, ...]:
    if requested_category:
        return (_normalize_category(requested_category),)
    try:
        return CONTENT_TYPE_CATEGORIES[content_type]
    except KeyError as exc:
        raise ValueError(f"content_type non supportato per allocator: {content_type!r}") from exc


def select_candidates(
    session: Session,
    *,
    content_type: str,
    requested_category: Optional[str] = None,
    selection_weeks: int | None = None,
    exclude_ids: set[int] | None = None,
) -> list[ReferenceItem]:
    """Reference pronte per un tipo contenuto, ordinate per consumo FIFO.

    La finestra temporale e' calcolata sulle settimane effettivamente presenti
    tra i candidati compatibili: ultime N settimane disponibili, poi ordine
    crescente (vecchio -> nuovo).
    """
    selection_weeks = selection_weeks or config.REFERENCE_SELECTION_WEEKS
    exclude_ids = exclude_ids or set()
    categories = categories_for(content_type, requested_category)

    assigned_ref_ids = {
        rid for (rid,) in session.execute(
            select(ContentPiece.reference_id).where(ContentPiece.reference_id.is_not(None))
        ).all()
        if rid is not None
    }
    blocked_ids = assigned_ref_ids | exclude_ids

    stmt = (
        select(ReferenceItem)
        .where(
            ReferenceItem.status == "ready",
            ReferenceItem.source_category.in_(categories),
            ReferenceItem.id.not_in(blocked_ids) if blocked_ids else ReferenceItem.id.is_not(None),
        )
    )
    if content_type == "carosello":
        stmt = stmt.where(ReferenceItem.content_type_hint == "carosello")
    elif content_type in ("video_talking", "video_balletti", "video_caption"):
        stmt = stmt.where(ReferenceItem.content_type_hint == "video")

    rows = list(session.scalars(stmt))
    rows = [r for r in rows if _has_required_media(r, content_type)]
    if not rows:
        return []

    weeks = sorted({r.week_start for r in rows if r.week_start is not None}, reverse=True)
    if weeks:
        allowed_weeks = set(weeks[:max(1, selection_weeks)])
        rows = [r for r in rows if r.week_start in allowed_weeks]

    return sorted(
        rows,
        key=lambda r: (
            r.week_start or r.imported_at.date(),
            r.sheet_order if r.sheet_order is not None else 10**9,
            r.id,
        ),
    )


def assign_references_to_plan(
    session: Session,
    plan_id: int,
    *,
    selection_weeks: int | None = None,
) -> AssignmentResult:
    pieces = list(
        session.scalars(
            select(ContentPiece)
            .where(ContentPiece.plan_week_id == plan_id, ContentPiece.reference_id.is_(None))
            .order_by(ContentPiece.id)
        )
    )
    used_ids: set[int] = set()
    assigned = 0
    by_type: dict[str, int] = {}

    for piece in pieces:
        candidates = select_candidates(
            session,
            content_type=piece.content_type,
            requested_category=piece.requested_source_category,
            selection_weeks=selection_weeks,
            exclude_ids=used_ids,
        )
        if not candidates:
            continue
        ref = candidates[0]
        piece.reference_id = ref.id
        used_ids.add(ref.id)
        assigned += 1
        by_type[piece.content_type] = by_type.get(piece.content_type, 0) + 1

    session.flush()
    return AssignmentResult(assigned=assigned, missing=len(pieces) - assigned, by_content_type=by_type)


def _has_required_media(ref: ReferenceItem, content_type: str) -> bool:
    if content_type in ("carosello", "stories"):
        return bool(ref.frame_paths)
    if content_type in ("video_talking", "video_balletti", "video_caption"):
        return bool(ref.local_video_path)
    return False


def _normalize_category(value: str) -> str:
    normalized = value.strip().upper().replace("_", " ")
    aliases = {
        "BALLETTI": "BALLETTI/LIPSYNC",
        "LIPSYNC": "BALLETTI/LIPSYNC",
        "BALLETY": "BALLETTI/LIPSYNC",
        "BALLETY/LIPSINC": "BALLETTI/LIPSYNC",
        "BALLETY LIPSINC": "BALLETTI/LIPSYNC",
    }
    return aliases.get(normalized, normalized)
