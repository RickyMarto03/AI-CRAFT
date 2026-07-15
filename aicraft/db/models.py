from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)

    profiles: Mapped[list["Profile"]] = relationship(back_populates="creator")


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    nome: Mapped[str]
    tipo_contenuto: Mapped[str]  # "solo_talking" | "solo_balletti" | "misto"
    attivo: Mapped[bool] = mapped_column(default=True)

    creator: Mapped["Creator"] = relationship(back_populates="profiles")
    content_pieces: Mapped[list["ContentPiece"]] = relationship(back_populates="profile")
    plan_weeks: Mapped[list["PlanWeek"]] = relationship(back_populates="profile")


class ReferenceItem(Base):
    __tablename__ = "reference_items"
    __table_args__ = (UniqueConstraint("source_url", name="uq_reference_items_source_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str]
    sheet_row_id: Mapped[Optional[str]]

    # "pending" | "downloading" | "downloaded" | "transcribing" | "ready" | "error"
    status: Mapped[str] = mapped_column(default="pending")

    local_video_path: Mapped[Optional[str]]
    local_audio_path: Mapped[Optional[str]]
    frame_paths: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    transcript: Mapped[Optional[str]]
    transcript_status: Mapped[Optional[str]]
    # Segmenti Whisper con timestamp: [{"start": float, "end": float, "text": str}, ...].
    # Non nel blueprint originale: aggiunta il 15/07/2026 per permettere a
    # write_talking_video_prompt di correlare dialogo e frame video per
    # secondo esatto invece di indovinare l'allineamento — vedi
    # docs/ai-craft-architecture.md §12.16. Puo' essere vuota per reference
    # scaricate prima di questa modifica (transcript resta comunque valido).
    transcript_segments: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    content_type_hint: Mapped[Optional[str]]  # "video" | "carosello"

    # Non nel blueprint originale: tag di contenuto letto dallo sheet
    # (es. BOOBS/BOOTY/GENERAL, TALKING...) e nome del tab di provenienza.
    # Vedi docs/ai-craft-architecture.md §7.
    source_category: Mapped[Optional[str]]
    source_tab: Mapped[Optional[str]]

    # Settimana/posizione lette dallo sheet. Servono alla libreria locale:
    # si scarica per settimana/categoria e poi si pesca dal DB in ordine
    # cronologico, senza chiedere all'utente di scegliere i link uno per uno.
    week_start: Mapped[Optional[dt.date]]
    week_end: Mapped[Optional[dt.date]]
    sheet_order: Mapped[Optional[int]]
    sheet_row: Mapped[Optional[int]]
    sheet_col: Mapped[Optional[int]]
    done_ricky_col: Mapped[Optional[int]]

    # Caption originale IG: il workflow deciso con l'utente prevede di
    # copiarla/adattarla, non inventarla da zero in Claude.
    original_caption: Mapped[Optional[str]]
    downloaded_at: Mapped[Optional[dt.datetime]]

    error_message: Mapped[Optional[str]]

    imported_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    content_pieces: Mapped[list["ContentPiece"]] = relationship(back_populates="reference")


class ContentPiece(Base):
    __tablename__ = "content_pieces"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    reference_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("reference_items.id"), nullable=True
    )

    # "video_talking" | "video_balletti" | "video_caption" | "carosello" | "stories"
    content_type: Mapped[str]
    plan_week_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plan_weeks.id"))
    scheduled_day: Mapped[Optional[str]]  # lun-dom

    status: Mapped[str] = mapped_column(default="reference_ready")
    generated_assets: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    caption: Mapped[Optional[str]]
    hashtags: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # Categoria richiesta per la reference sorgente, quando serve un vincolo
    # esplicito (es. "carosello BOOBS"). Se vuoto, l'allocator sceglie la
    # categoria naturale del content_type.
    requested_source_category: Mapped[Optional[str]]

    cost_credits_estimated: Mapped[Optional[float]]
    cost_credits_actual: Mapped[Optional[float]]

    created_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    profile: Mapped["Profile"] = relationship(back_populates="content_pieces")
    reference: Mapped[Optional["ReferenceItem"]] = relationship(back_populates="content_pieces")
    plan_week: Mapped[Optional["PlanWeek"]] = relationship(back_populates="content_pieces")
    ledger_entries: Mapped[list["CreditLedger"]] = relationship(back_populates="content_piece")


class ContentPieceEvent(Base):
    """Log storico degli stadi di un ContentPiece durante la produzione, con
    timestamp e durata. Non nel blueprint originale: aggiunto su richiesta
    dell'utente (15/07/2026) — prima si vedeva solo lo status corrente, non
    quanto ci ha messo ogni stadio o dove un pezzo si e' eventualmente
    bloccato. Scritto da engine.process_content_piece ad ogni inizio/fine
    stadio, non modificabile da altrove. Vedi docs/ai-craft-architecture.md
    §18."""

    __tablename__ = "content_piece_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_piece_id: Mapped[int] = mapped_column(ForeignKey("content_pieces.id"))
    stage: Mapped[str]  # "image_regen" | "video_regen" | "qa" | "caption_hashtag" | "delivery" | "delivered"
    status: Mapped[str]  # "started" | "completed" | "failed"
    detail: Mapped[Optional[str]]  # messaggio d'errore/nota, solo su "failed"
    duration_seconds: Mapped[Optional[float]]  # valorizzato solo su "completed"/"failed"
    timestamp: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)


class PlanWeek(Base):
    __tablename__ = "plan_weeks"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    week_start: Mapped[dt.date]
    week_end: Mapped[dt.date]
    status: Mapped[str] = mapped_column(default="bozza")  # "bozza" | "approvato"
    version: Mapped[int] = mapped_column(default=1)

    profile: Mapped["Profile"] = relationship(back_populates="plan_weeks")
    content_pieces: Mapped[list["ContentPiece"]] = relationship(back_populates="plan_week")


class CreditLedger(Base):
    __tablename__ = "credit_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)
    delta_credits: Mapped[float]
    motivo: Mapped[str]
    content_piece_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("content_pieces.id"), nullable=True
    )

    content_piece: Mapped[Optional["ContentPiece"]] = relationship(back_populates="ledger_entries")


class AppState(Base):
    """Stato app livello-operatore (key/value). Non nel blueprint originale:
    aggiunto per lo Step 5 per memorizzare il "profilo attivo selezionato"
    (diverso da Profile.attivo, che indica se un profilo e' abilitato). Vedi
    docs/ai-craft-architecture.md §10."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[Optional[str]]


class ImprovementNote(Base):
    """Backlog di cose da migliorare/aggiungere, visibile nell'app (sezione
    dedicata). Non nel blueprint originale: aggiunto su richiesta
    dell'utente (15/07/2026) — ogni volta che durante il lavoro emerge un
    limite noto o un miglioramento possibile ma fuori scope del momento, va
    registrato qui invece che solo nei commenti/doc tecnici, cosi' resta
    consultabile dall'operatore senza dover leggere codice. Vedi
    docs/ai-craft-architecture.md §12."""

    __tablename__ = "improvement_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=dt.datetime.utcnow)
    category: Mapped[str]  # testo libero, es. "qualita'", "limite noto", "funzionalita'"
    title: Mapped[str]
    description: Mapped[Optional[str]]
    status: Mapped[str] = mapped_column(default="aperto")  # "aperto" | "fatto" | "scartato"
