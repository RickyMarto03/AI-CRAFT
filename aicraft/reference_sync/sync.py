"""Orchestratore del Reference Sync: sheet (read-only) -> DB -> download -> trascrizione.

Un fallimento su un singolo ReferenceItem non deve bloccare gli altri:
ogni item e' processato in isolamento, l'errore viene salvato su
ReferenceItem.error_message/status e lo scan prosegue con il prossimo.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db.base import SessionLocal, init_db
from ..db.models import ReferenceItem
from . import downloader, transcriber
from .sheets_reader import SheetClient, SheetReference, fetch_references

logger = logging.getLogger(__name__)


def upsert_reference(session: Session, ref: SheetReference) -> ReferenceItem:
    existing = session.scalar(
        select(ReferenceItem).where(ReferenceItem.source_url == ref.url)
    )
    if existing:
        existing.sheet_row_id = ref.sheet_row_id
        existing.source_category = ref.source_category
        existing.source_tab = ref.source_tab
        existing.content_type_hint = ref.content_type_hint
        return existing

    item = ReferenceItem(
        source_url=ref.url,
        sheet_row_id=ref.sheet_row_id,
        source_category=ref.source_category,
        source_tab=ref.source_tab,
        content_type_hint=ref.content_type_hint,
        status="pending",
    )
    session.add(item)
    return item


def process_item(session: Session, item: ReferenceItem) -> None:
    try:
        item.status = "downloading"
        session.commit()

        result = downloader.download_reference(item.source_url)
        item.local_video_path = str(result.video_path) if result.video_path else None
        item.frame_paths = [str(p) for p in result.image_paths]
        item.status = "downloaded"
        session.commit()

        if result.video_path:
            item.status = "transcribing"
            item.transcript_status = "running"
            session.commit()

            transcript, audio_path = transcriber.transcribe_video(result.video_path)
            item.local_audio_path = str(audio_path) if audio_path else None
            item.transcript = transcript
            # "empty" = video muto/senza parlato (caso legittimo), distinto da "done"
            item.transcript_status = "done" if transcript else "empty"

        item.status = "ready"
        item.error_message = None
        session.commit()

    except Exception as exc:
        logger.exception("Errore durante il processing di %s", item.source_url)
        session.rollback()
        item.status = "error"
        item.error_message = str(exc)
        session.commit()


def run_once(year: int | None = None) -> None:
    init_db()
    year = year or dt.date.today().year

    client = SheetClient(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_SHEET_ID)
    refs = fetch_references(client, config.GOOGLE_SHEET_TABS, year=year)
    logger.info("Lette %d reference dallo sheet (%s)", len(refs), ", ".join(config.GOOGLE_SHEET_TABS))

    with SessionLocal() as session:
        items = [upsert_reference(session, ref) for ref in refs]
        session.commit()

        pending_items = [item for item in items if item.status in ("pending", "error")]
        logger.info("%d reference da processare (download + trascrizione)", len(pending_items))

        for item in pending_items:
            process_item(session, item)
