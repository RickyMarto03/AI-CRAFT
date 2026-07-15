"""Entrypoint CLI del Reference Sync.

Uso:
    python -m aicraft.reference_sync.run              # un solo giro
    python -m aicraft.reference_sync.run --loop        # loop continuo (scheduler minimale)
    python -m aicraft.reference_sync.run --loop --interval 600
"""

from __future__ import annotations

import argparse
import logging
import time

from .sync import run_once


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="AI-craft Reference Sync")
    parser.add_argument("--loop", action="store_true", help="esegue in loop continuo invece di un solo giro")
    parser.add_argument("--interval", type=int, default=300, help="secondi di attesa tra un giro e l'altro in modalita' --loop")
    args = parser.parse_args()

    if not args.loop:
        run_once()
        return

    while True:
        run_once()
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
