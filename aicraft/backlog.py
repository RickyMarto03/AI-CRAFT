"""Backlog di miglioramenti/limiti noti, consultabile dall'app (sezione
dedicata). Aggiunto su richiesta dell'utente (15/07/2026): ogni volta che
durante il lavoro emerge qualcosa di migliorabile ma fuori scope del
momento, va registrato qui con `add_note` invece che solo nei commenti/doc
tecnici — cosi' resta consultabile senza leggere codice. Vedi
docs/ai-craft-architecture.md §12.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db.models import ImprovementNote

STATI_VALIDI = ("aperto", "fatto", "scartato")


def add_note(session: Session, *, category: str, title: str, description: str = "") -> ImprovementNote:
    note = ImprovementNote(category=category, title=title, description=description or None, status="aperto")
    session.add(note)
    session.flush()
    return note


def list_notes(session: Session, *, status: str | None = "aperto", search: str | None = None) -> list:
    """`search` filtra per sottostringa (case-insensitive) in titolo,
    descrizione o categoria — aggiunto il 15/07/2026 su richiesta
    dell'utente: il backlog cresce nel tempo, serviva un modo per
    ritrovare una voce senza scorrere tutto."""
    stmt = select(ImprovementNote).order_by(ImprovementNote.created_at.desc())
    if status is not None:
        stmt = stmt.where(ImprovementNote.status == status)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(
            ImprovementNote.title.ilike(like),
            ImprovementNote.description.ilike(like),
            ImprovementNote.category.ilike(like),
        ))
    return list(session.scalars(stmt))


def set_status(session: Session, note_id: int, status: str) -> ImprovementNote:
    if status not in STATI_VALIDI:
        raise ValueError(f"status non valido: {status!r} (attesi {STATI_VALIDI})")
    note = session.get(ImprovementNote, note_id)
    if note is None:
        raise ValueError(f"ImprovementNote {note_id} inesistente")
    note.status = status
    session.flush()
    return note
