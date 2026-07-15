"""Entrypoint CLI del Production Engine.

Uso:
    python -m aicraft.production.run
"""

from __future__ import annotations

import logging

from ..db.base import SessionLocal, init_db
from .engine import run_once


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    with SessionLocal() as session:
        run_once(session)


if __name__ == "__main__":
    main()
