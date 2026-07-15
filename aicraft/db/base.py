from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ..config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  registra le tabelle su Base.metadata

    Base.metadata.create_all(engine)
    _run_additive_migrations()


def _run_additive_migrations() -> None:
    """Migrazioni minime per il DB locale esistente.

    Il progetto non usa ancora Alembic; `create_all()` crea le nuove colonne
    solo su DB nuovi, ma non altera `data/aicraft.db` gia' presente. Queste
    aggiunte sono tutte nullable, quindi sono sicure e idempotenti.
    """
    inspector = inspect(engine)
    if "reference_items" in inspector.get_table_names():
        _add_missing_columns(
            "reference_items",
            {
                "week_start": "DATE",
                "week_end": "DATE",
                "sheet_order": "INTEGER",
                "sheet_row": "INTEGER",
                "sheet_col": "INTEGER",
                "done_ricky_col": "INTEGER",
                "original_caption": "TEXT",
                "downloaded_at": "DATETIME",
                "transcript_segments": "TEXT",
                "download_attempts": "INTEGER",
            },
        )
        # Backfill: le righe esistenti prendono NULL da ALTER TABLE ADD
        # COLUMN (SQLite non applica un default a colonne gia' popolate).
        # 0 = "nessun tentativo ancora contato con la nuova logica", coerente
        # col default Python per le righe nuove. Idempotente: dopo il primo
        # giro non ci sono piu' righe NULL da aggiornare.
        with engine.begin() as conn:
            conn.execute(text("UPDATE reference_items SET download_attempts = 0 WHERE download_attempts IS NULL"))
    if "content_pieces" in inspector.get_table_names():
        _add_missing_columns(
            "content_pieces",
            {
                "requested_source_category": "VARCHAR",
                "was_refused": "BOOLEAN",
                "quality_rating": "INTEGER",
                "priority": "INTEGER",
            },
        )
        with engine.begin() as conn:
            conn.execute(text("UPDATE content_pieces SET was_refused = 0 WHERE was_refused IS NULL"))
            conn.execute(text("UPDATE content_pieces SET priority = 0 WHERE priority IS NULL"))
    if "plan_weeks" in inspector.get_table_names():
        _add_missing_columns(
            "plan_weeks",
            {
                "created_at": "DATETIME",
                "updated_at": "DATETIME",
            },
        )


def _add_missing_columns(table_name: str, columns: dict[str, str]) -> None:
    existing = {c["name"] for c in inspect(engine).get_columns(table_name)}
    missing = [(name, ddl) for name, ddl in columns.items() if name not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for name, ddl in missing:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))
