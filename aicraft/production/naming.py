"""Convenzioni di naming e struttura cartelle per gli output di produzione.

FISSE per regola di progetto (CLAUDE.md §"Regole ferme"): non sono decise
da Claude a runtime, sono definite qui una volta sola. Se cambia qualcosa,
si cambia qui, non nel prompt di uno stadio creativo.

Struttura:
    data/delivery/{profile-slug}/{content_type}/{week-start}_{scheduled_day}_{piece_id}/
        asset_01.<ext>, asset_02.<ext>, ...
        caption.txt
        meta.json
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import config

CAPTION_FILENAME = "caption.txt"
META_FILENAME = "meta.json"


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "senza-nome"


def content_piece_folder(*, profile_nome: str, content_type: str, piece_id: int, week_start: str | None, scheduled_day: str | None) -> Path:
    week_part = week_start or "no-week"
    day_part = scheduled_day or "no-day"
    return (
        config.DELIVERY_DIR
        / slugify(profile_nome)
        / slugify(content_type)
        / f"{week_part}_{day_part}_{piece_id}"
    )


def asset_filename(index: int, extension: str) -> str:
    return f"asset_{index:02d}.{extension.lstrip('.')}"
