"""Orchestratore del Reference Sync: sheet -> DB -> download -> mark sheet -> trascrizione.

Un fallimento su un singolo ReferenceItem non deve bloccare gli altri:
ogni item e' processato in isolamento, l'errore viene salvato su
ReferenceItem.error_message/status e lo scan prosegue con il prossimo.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db.base import SessionLocal, init_db
from ..db.models import ContentPiece, ReferenceItem
from . import downloader, transcriber
from .sheets_reader import SheetClient, SheetReference, fetch_references

logger = logging.getLogger(__name__)

# Stati di un ReferenceItem che vale la pena ritentare in un run di sync
# (non ancora scaricato, fallito con un errore recuperabile, o rimasto a
# meta' per un crash/interruzione precedente). Unica fonte di verita' usata
# sia da run_once che da run_policy_once: prima del fix del 15/07/2026
# run_once() aveva una tupla piu' corta che non includeva gli stati
# granulari introdotti da _status_for_processing_error
# (download_error/unavailable/private/transcription_error), quindi un item
# fallito con quegli stati restava bloccato per sempre con `references
# sync` mentre veniva ritentato solo con `sync-policy` — bug reale trovato
# in review, corretto unificando le due liste qui.
RETRYABLE_STATUSES = (
    "pending",
    "error",
    "download_error",
    "private",
    "unavailable",
    "transcription_error",
    "downloading",
    "transcribing",
)

# Sottoinsieme di RETRYABLE_STATUSES che rappresenta un fallimento vero e
# proprio (non "mai ancora provato" come pending/downloading/transcribing).
# Unica fonte di verita' condivisa con la UI (prima duplicata come
# `_ERROR_STATUSES` in desktop/api.py — stesso principio del fix sopra:
# tenerla in un solo posto evita che le due viste si disallineino su quali
# stati contano come errore).
ERROR_STATUSES = (
    "error",
    "download_error",
    "private",
    "unavailable",
    "transcription_error",
)


@dataclass(frozen=True)
class SyncPolicyItem:
    source_tab: str
    source_category: str
    limit: int


def _safe_segment(value: str | None) -> str:
    text = (value or "UNKNOWN").strip().replace(" ", "_").replace("/", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch in ("_", "-", ".")).upper() or "UNKNOWN"


def _week_slug(ref: SheetReference | ReferenceItem) -> str:
    week_start = ref.week_start
    if week_start is None:
        return "UNKNOWN_WEEK"
    iso = week_start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def media_folder_for_reference(ref: SheetReference) -> Path:
    shortcode = downloader.shortcode_from_url(ref.url)
    return (
        config.MEDIA_DIR
        / _week_slug(ref)
        / _safe_segment(ref.source_tab)
        / _safe_segment(ref.source_category)
        / shortcode
    )


def _carousel_mark_color() -> tuple[float, float, float]:
    parts = [p.strip() for p in config.GOOGLE_SHEET_CAROUSEL_MARK_COLOR.split(",")]
    if len(parts) != 3:
        return (1.0, 0.95, 0.65)
    try:
        return tuple(max(0.0, min(1.0, float(p))) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return (1.0, 0.95, 0.65)


def _within_retention(ref: SheetReference, *, today: dt.date) -> bool:
    if ref.week_end is None:
        return True
    cutoff = today - dt.timedelta(days=config.REFERENCE_RETENTION_DAYS)
    return ref.week_end >= cutoff


def parse_sync_policy(policy: str | None = None) -> list[SyncPolicyItem]:
    """Parse policy tipo ``CAROSELLI:BOOBS=10,VIRAL GENERAL:TALKING=5``."""
    policy = policy if policy is not None else config.REFERENCE_SYNC_POLICY
    items: list[SyncPolicyItem] = []
    for chunk in (policy or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        left, sep, limit_text = chunk.partition("=")
        if not sep:
            raise ValueError(f"Policy sync non valida (manca '='): {chunk!r}")
        tab, sep, category = left.partition(":")
        if not sep:
            raise ValueError(f"Policy sync non valida (manca ':'): {chunk!r}")
        limit = int(limit_text)
        if limit < 0:
            raise ValueError(f"Policy sync con limite negativo: {chunk!r}")
        items.append(SyncPolicyItem(tab.strip().upper(), category.strip().upper(), limit))
    return items


def upsert_reference(session: Session, ref: SheetReference) -> ReferenceItem:
    existing = session.scalar(
        select(ReferenceItem).where(ReferenceItem.source_url == ref.url)
    )
    if existing:
        existing.sheet_row_id = ref.sheet_row_id
        existing.source_category = ref.source_category
        existing.source_tab = ref.source_tab
        existing.content_type_hint = ref.content_type_hint
        existing.week_start = ref.week_start
        existing.week_end = ref.week_end
        existing.sheet_order = ref.sheet_order
        existing.sheet_row = ref.sheet_row
        existing.sheet_col = ref.sheet_col
        existing.done_ricky_col = ref.done_ricky_col
        return existing

    item = ReferenceItem(
        source_url=ref.url,
        sheet_row_id=ref.sheet_row_id,
        source_category=ref.source_category,
        source_tab=ref.source_tab,
        content_type_hint=ref.content_type_hint,
        week_start=ref.week_start,
        week_end=ref.week_end,
        sheet_order=ref.sheet_order,
        sheet_row=ref.sheet_row,
        sheet_col=ref.sheet_col,
        done_ricky_col=ref.done_ricky_col,
        status="pending",
    )
    session.add(item)
    return item


def process_item(
    session: Session,
    item: ReferenceItem,
    *,
    sheet_ref: SheetReference | None = None,
    sheet_client: SheetClient | None = None,
) -> None:
    try:
        item.status = "downloading"
        session.commit()

        folder = media_folder_for_reference(sheet_ref) if sheet_ref is not None else None
        result = downloader.download_reference(item.source_url, folder=folder)
        item.local_video_path = str(result.video_path) if result.video_path else None
        item.frame_paths = [str(p) for p in result.image_paths]
        item.original_caption = result.original_caption
        item.downloaded_at = dt.datetime.utcnow()
        item.status = "downloaded"
        session.commit()

        if config.GOOGLE_SHEET_MARK_DOWNLOADS and sheet_client is not None and sheet_ref is not None:
            try:
                sheet_client.mark_downloaded(sheet_ref, carousel_color=_carousel_mark_color())
            except Exception as exc:  # noqa: BLE001 — download locale riuscito, non lo invalidiamo
                logger.warning("Reference scaricata ma mark sheet fallito per %s: %s", item.source_url, exc)

        if result.video_path:
            item.status = "transcribing"
            item.transcript_status = "running"
            session.commit()

            transcript, segments, audio_path = transcriber.transcribe_video(result.video_path)
            item.local_audio_path = str(audio_path) if audio_path else None
            item.transcript = transcript
            item.transcript_segments = segments
            # "empty" = video muto/senza parlato (caso legittimo), distinto da "done"
            item.transcript_status = "done" if transcript else "empty"

        item.status = "ready"
        item.error_message = None
        session.commit()

    except Exception as exc:
        logger.exception("Errore durante il processing di %s", item.source_url)
        session.rollback()
        item.status = _status_for_processing_error(exc, current_status=item.status)
        item.error_message = str(exc)
        session.commit()


def _status_for_processing_error(exc: Exception, *, current_status: str | None = None) -> str:
    names = {exc.__class__.__name__}
    cause = exc.__cause__
    context = exc.__context__
    while cause is not None:
        names.add(cause.__class__.__name__)
        cause = cause.__cause__
    while context is not None:
        names.add(context.__class__.__name__)
        context = context.__context__

    text = str(exc).lower()
    if current_status == "transcribing" or "whisper" in text or "ffmpeg" in text:
        return "transcription_error"
    if names & {"MediaNotFound", "MediaUnavailable", "ClientNotFoundError"}:
        return "unavailable"
    if names & {"ClientUnauthorizedError", "LoginRequired", "PleaseWaitFewMinutes"}:
        return "private"
    if "not found or unavailable" in text or "media not found" in text:
        return "unavailable"
    if "unauthorized" in text or "login" in text or "private" in text:
        return "private"
    return "download_error"


def cleanup_old_references(session: Session, *, today: dt.date | None = None) -> int:
    """Elimina dal DB/file locali i reference IG oltre retention.

    Tocca solo materiale originale in `ReferenceItem`; non cancella asset
    generati/consegnati. Eventuali ContentPiece storici vengono scollegati
    dalla reference prima di cancellarla.
    """
    today = today or dt.date.today()
    cutoff = today - dt.timedelta(days=config.REFERENCE_RETENTION_DAYS)
    old_items = session.scalars(
        select(ReferenceItem).where(ReferenceItem.week_end.is_not(None), ReferenceItem.week_end < cutoff)
    ).all()

    deleted = 0
    for item in old_items:
        for piece in session.scalars(select(ContentPiece).where(ContentPiece.reference_id == item.id)):
            piece.reference_id = None

        paths = []
        if item.local_video_path:
            paths.append(Path(item.local_video_path))
        if item.local_audio_path:
            paths.append(Path(item.local_audio_path))
        for frame in item.frame_paths or []:
            paths.append(Path(frame))
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Impossibile eliminare file reference vecchio %s: %s", path, exc)
            _remove_empty_parents(path.parent)

        session.delete(item)
        deleted += 1

    session.flush()
    return deleted


def _remove_empty_parents(start: Path) -> None:
    media_root = config.MEDIA_DIR.resolve()
    current = start
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            return
        if resolved == media_root or media_root not in resolved.parents:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def retry_reference(reference_id: int) -> dict:
    """Riprova il download/trascrizione di UNA reference specifica (azione
    manuale dalla Libreria), usando l'URL/categoria gia' salvati sul
    ReferenceItem — non serve un nuovo giro di lettura sheet, quindi NON
    marca lo Sheet (`process_item` senza sheet_ref/sheet_client si limita a
    non provarci, vedi il guard su GOOGLE_SHEET_MARK_DOWNLOADS). Utile per
    sbloccare un item fallito (download_error/private/unavailable/
    transcription_error) senza aspettare il prossimo sync completo."""
    init_db()
    with SessionLocal() as session:
        item = session.get(ReferenceItem, reference_id)
        if item is None:
            raise ValueError(f"ReferenceItem {reference_id} inesistente")
        process_item(session, item)
        return {"id": item.id, "status": item.status, "error_message": item.error_message}


def retry_all(reference_ids: list) -> dict:
    """Riprova in sequenza una lista di reference (stesso `retry_reference`
    per ciascuna, quindi stesso rate-limit del download singolo — nessun
    trucco per andare piu' veloci, per non rischiare di farci bloccare da
    Instagram). Pensata per il bottone "Riprova tutti" della Libreria: la
    lista di ID da ritentare la decide il chiamante (es. tutte quelle in
    uno stato ritentabile, eventualmente filtrate per categoria)."""
    ready = 0
    still_failed = 0
    for reference_id in reference_ids:
        result = retry_reference(reference_id)
        if result["status"] == "ready":
            ready += 1
        else:
            still_failed += 1
    return {"total": len(reference_ids), "ready": ready, "still_failed": still_failed}


def retry_stale_errors(*, older_than_days: int = 3) -> dict:
    """Ritenta automaticamente le reference fallite (`ERROR_STATUSES`) non
    toccate da almeno `older_than_days` giorni — pensata per lo scheduler
    settimanale, richiesto dall'utente (15/07/2026): un fallimento
    transitorio (rate-limit/timeout Instagram) spesso si risolve da solo
    col tempo, ha senso ritentarlo automaticamente invece di lasciarlo
    bloccato finche' l'operatore non lo nota a mano in Libreria. Le
    reference fallite di recente (entro la finestra) NON vengono
    ritoccate, per non insistere a raffica sulla stessa reference appena
    fallita."""
    init_db()
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=older_than_days)
    with SessionLocal() as session:
        ids = list(session.scalars(
            select(ReferenceItem.id).where(
                ReferenceItem.status.in_(ERROR_STATUSES),
                ReferenceItem.updated_at <= cutoff,
            )
        ))
    result = retry_all(ids)
    result["older_than_days"] = older_than_days
    return result


def run_once(
    year: int | None = None,
    *,
    max_items: int | None = None,
    source_tab: str | None = None,
    source_category: str | None = None,
) -> dict:
    init_db()
    year = year or dt.date.today().year
    today = dt.date.today()
    if max_items is None:
        max_items = config.REFERENCE_SYNC_MAX_ITEMS

    client = SheetClient(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_SHEET_ID)
    refs = [
        ref for ref in fetch_references(client, config.GOOGLE_SHEET_TABS, year=year)
        if _within_retention(ref, today=today)
    ]
    if source_tab:
        tab_filter = source_tab.strip().upper()
        refs = [ref for ref in refs if ref.source_tab.upper() == tab_filter]
    if source_category:
        category_filter = source_category.strip().upper()
        refs = [ref for ref in refs if ref.source_category.upper() == category_filter]
    logger.info("Lette %d reference dallo sheet (%s)", len(refs), ", ".join(config.GOOGLE_SHEET_TABS))

    with SessionLocal() as session:
        pairs = [(upsert_reference(session, ref), ref) for ref in refs]
        session.commit()

        cleanup_count = cleanup_old_references(session, today=today)
        if cleanup_count:
            logger.info("Eliminate %d reference IG oltre retention (%d giorni)", cleanup_count, config.REFERENCE_RETENTION_DAYS)
            session.commit()

        pending_pairs = [(item, ref) for item, ref in pairs if item.status in RETRYABLE_STATUSES]
        total_pending = len(pending_pairs)
        if max_items and max_items > 0:
            pending_pairs = pending_pairs[:max_items]
        logger.info(
            "%d reference da processare (download + trascrizione), su %d candidati",
            len(pending_pairs),
            total_pending,
        )

        for item, ref in pending_pairs:
            process_item(session, item, sheet_ref=ref, sheet_client=client)

        return {
            "sheet_refs": len(refs),
            "pending_total": total_pending,
            "processed": len(pending_pairs),
            "cleanup_deleted": cleanup_count,
        }


def run_policy_once(year: int | None = None, *, policy: str | None = None, retry_stale_after_days: int | None = 3) -> dict:
    init_db()
    year = year or dt.date.today().year
    today = dt.date.today()
    policy_items = parse_sync_policy(policy)

    client = SheetClient(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_SHEET_ID)
    refs = [
        ref for ref in fetch_references(client, config.GOOGLE_SHEET_TABS, year=year)
        if _within_retention(ref, today=today)
    ]
    logger.info("Lette %d reference dallo sheet per sync policy", len(refs))

    with SessionLocal() as session:
        pairs = [(upsert_reference(session, ref), ref) for ref in refs]
        session.commit()

        cleanup_count = cleanup_old_references(session, today=today)
        if cleanup_count:
            logger.info("Eliminate %d reference IG oltre retention (%d giorni)", cleanup_count, config.REFERENCE_RETENTION_DAYS)
            session.commit()

        processed = 0
        by_policy = []
        for item_policy in policy_items:
            candidates = [
                (item, ref) for item, ref in pairs
                if ref.source_tab.upper() == item_policy.source_tab
                and ref.source_category.upper() == item_policy.source_category
                and item.status in RETRYABLE_STATUSES
            ][:item_policy.limit]
            for item, ref in candidates:
                process_item(session, item, sheet_ref=ref, sheet_client=client)
            processed += len(candidates)
            by_policy.append({
                "tab": item_policy.source_tab,
                "category": item_policy.source_category,
                "limit": item_policy.limit,
                "processed": len(candidates),
            })

        result = {
            "sheet_refs": len(refs),
            "processed": processed,
            "cleanup_deleted": cleanup_count,
            "policy": by_policy,
        }

    # Fuori dal `with` sopra: retry_stale_errors gestisce la propria sessione
    # (stesso motivo per cui non e' contato nei limiti per-categoria della
    # policy: e' una preoccupazione distinta — "riprova i vecchi falliti",
    # non "scarica altro materiale nuovo" — quindi non deve competere per
    # gli stessi slot). Richiesto dall'utente (15/07/2026) per lo scheduler
    # settimanale: un fallimento transitorio spesso si risolve da solo col
    # tempo, ha senso ritentarlo automaticamente.
    if retry_stale_after_days is not None:
        result["retry_stale"] = retry_stale_errors(older_than_days=retry_stale_after_days)
    return result
