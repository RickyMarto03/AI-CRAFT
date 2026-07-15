"""Backup locale del DB SQLite prima di operazioni massive (sync/produzione)
o su richiesta manuale dall'app — richiesto dall'utente (15/07/2026) per
poter tornare indietro in caso di bug, invece di scoprire un problema dopo
che il DB e' gia' stato modificato. Copia `data/aicraft.db` in
`data/backups/` con timestamp nel nome, tenendo solo gli ultimi
MAX_BACKUPS (i piu' vecchi vengono eliminati automaticamente).
"""

from __future__ import annotations

import datetime as dt
import logging
import shutil
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

BACKUP_DIR = config.DATA_DIR / "backups"
MAX_BACKUPS = 14

_SQLITE_PREFIX = "sqlite:///"


def _db_path() -> Path | None:
    """None per DB non-sqlite (es. `sqlite:///:memory:` nei test): niente
    file reale da copiare, non e' un errore."""
    url = config.DATABASE_URL
    if not url.startswith(_SQLITE_PREFIX):
        return None
    path = Path(url[len(_SQLITE_PREFIX):])
    if str(path) in (":memory:", ""):
        return None
    return path


def run_backup() -> dict:
    db_path = _db_path()
    if db_path is None or not db_path.exists():
        return {"ok": False, "reason": "Nessun DB SQLite locale da copiare"}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"aicraft_{stamp}.db"
    shutil.copy2(db_path, dest)

    backups = sorted(BACKUP_DIR.glob("aicraft_*.db"))
    removed = 0
    for old in backups[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)
        removed += 1

    return {"ok": True, "path": str(dest), "kept": min(len(backups), MAX_BACKUPS), "removed": removed}


def run_backup_safe() -> dict:
    """Come run_backup, ma non solleva mai — pensata per essere chiamata
    "di passaggio" prima di sync/produzione senza rischiare di bloccare
    l'operazione principale se il backup stesso fallisce (es. disco pieno)."""
    try:
        return run_backup()
    except Exception as exc:  # noqa: BLE001 — un backup fallito non deve fermare sync/produzione
        logger.warning("Backup DB fallito (si prosegue comunque): %s", exc)
        return {"ok": False, "reason": str(exc)}
