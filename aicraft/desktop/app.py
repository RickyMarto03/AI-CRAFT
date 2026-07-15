"""Launcher dell'app desktop AI-craft (PyWebView).

    python -m aicraft.desktop.app

Crea una finestra nativa macOS (backend Cocoa) che carica il frontend in
`web/` ed espone `Api` (aicraft/desktop/api.py) come `window.pywebview.api`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import webview

from .api import get_api

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX = WEB_DIR / "index.html"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    api = get_api()
    webview.create_window(
        "AI-craft — Centro di Comando",
        url=str(INDEX),
        js_api=api,
        width=1400,
        height=900,
        min_size=(1100, 720),
        background_color="#0a0e14",
    )
    webview.start()


if __name__ == "__main__":
    main()
