"""Stadio deterministico: assembla la cartella finale di consegna per un
ContentPiece usando la convenzione di naming fissa in naming.py. Nessuna
decisione creativa qui — solo copia file e scrittura di caption/meta gia'
prodotti dagli stadi precedenti.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from ..db.models import ContentPiece, Profile
from . import naming


def deliver(piece: ContentPiece, profile: Profile, asset_paths: list) -> tuple:
    week_start = piece.plan_week.week_start.isoformat() if piece.plan_week else None
    folder = naming.content_piece_folder(
        profile_nome=profile.nome,
        content_type=piece.content_type,
        piece_id=piece.id,
        week_start=week_start,
        scheduled_day=piece.scheduled_day,
    )
    folder.mkdir(parents=True, exist_ok=True)

    delivered_assets = []
    for index, src in enumerate(asset_paths, start=1):
        src_path = Path(src)
        if not src_path.exists():
            # asset remoto (es. result_url Higgsfield non ancora scaricato
            # localmente): registrato cosi' com'e', nessuna copia da fare.
            delivered_assets.append(str(src))
            continue
        dest = folder / naming.asset_filename(index, src_path.suffix or ".bin")
        shutil.copy2(src_path, dest)
        delivered_assets.append(str(dest))

    if piece.caption:
        (folder / naming.CAPTION_FILENAME).write_text(piece.caption, encoding="utf-8")

    meta = {
        "content_piece_id": piece.id,
        "content_type": piece.content_type,
        "hashtags": piece.hashtags or [],
        "cost_credits_actual": piece.cost_credits_actual,
        "reference_id": piece.reference_id,
    }
    (folder / naming.META_FILENAME).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return folder, delivered_assets
