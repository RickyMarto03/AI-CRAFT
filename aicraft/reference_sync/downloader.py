"""Download dei reference IG via instagrapi.

Perche' instagrapi e non Instaloader/yt-dlp/gallery-dl: da luglio 2026
Instagram blocca le query GraphQL del sito web che quei tre usano (vedi
docs/ai-craft-architecture.md §7). instagrapi colpisce invece l'API "mobile"
(quella dell'app), endpoint diversi e non bloccati. Verificato il 14/07/2026:
5/5 su link reali, download reali di video e caroselli funzionanti, dove gli
altri facevano 0/15.

Autenticazione: nessuna password in codice. Si riusa il cookie `sessionid`
del browser locale gia' loggato su instagram.com (via browser_cookie3), che
instagrapi accetta con `login_by_sessionid`. Le impostazioni del device
vengono salvate su disco per mantenere un fingerprint stabile tra i run.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .. import config

logger = logging.getLogger(__name__)

_DEFAULT_MIN_DELAY_SECONDS = 3.0

# instagrapi media_type
_PHOTO, _VIDEO, _ALBUM = 1, 2, 8

_client = None  # riuso lo stesso client per tutta la durata del processo


@dataclass
class DownloadResult:
    folder: Path
    video_path: Path | None
    image_paths: list[Path]
    original_caption: str | None = None


def shortcode_from_url(url: str) -> str:
    # gestisce query string e img_index: /p/<code>/?img_index=2 -> <code>
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("p", "reel", "reels", "tv"):
        return parts[1]
    return parts[-1]


_shortcode_from_url = shortcode_from_url


def _browser_cookies() -> dict:
    import browser_cookie3

    browser_fn = getattr(browser_cookie3, config.INSTAGRAM_BROWSER, None)
    if browser_fn is None:
        raise RuntimeError(
            f"Browser '{config.INSTAGRAM_BROWSER}' non supportato da browser_cookie3 "
            "(valori tipici: chrome, safari, firefox, edge, brave)"
        )
    return {c.name: c.value for c in browser_fn(domain_name="instagram.com")}


def _get_client():
    global _client
    if _client is not None:
        return _client

    from instagrapi import Client

    cl = Client()
    settings_file = config.INSTAGRAM_SESSION_DIR / "instagrapi_settings.json"
    if settings_file.exists():
        try:
            cl.load_settings(settings_file)
        except Exception:
            logger.warning("Settings instagrapi non leggibili, li rigenero")

    sessionid = _browser_cookies().get("sessionid")
    if not sessionid:
        raise RuntimeError(
            f"Nessun cookie 'sessionid' di instagram.com trovato in {config.INSTAGRAM_BROWSER}: "
            "assicurati di essere loggato su instagram.com in quel browser."
        )

    cl.login_by_sessionid(sessionid)
    cl.dump_settings(settings_file)
    logger.info("Sessione instagrapi attiva per user_id %s", cl.user_id)

    _client = cl
    return cl


def download_reference(
    url: str,
    min_delay_seconds: float = _DEFAULT_MIN_DELAY_SECONDS,
    folder: Path | None = None,
) -> DownloadResult:
    cl = _get_client()
    shortcode = shortcode_from_url(url)
    folder = folder or (config.MEDIA_DIR / shortcode)
    folder.mkdir(parents=True, exist_ok=True)

    pk = cl.media_pk_from_code(shortcode)
    info = cl.media_info(pk)
    original_caption = getattr(info, "caption_text", None) or getattr(info, "caption", None)
    if original_caption is not None:
        original_caption = str(original_caption).strip() or None

    video_path: Optional[Path] = None
    image_paths: list = []

    if info.media_type == _PHOTO:
        image_paths = [Path(cl.photo_download(pk, folder=folder))]
    elif info.media_type == _VIDEO:
        video_path = Path(cl.video_download(pk, folder=folder))
    elif info.media_type == _ALBUM:
        for p in map(Path, cl.album_download(pk, folder=folder)):
            if p.suffix.lower() == ".mp4":
                video_path = video_path or p
            else:
                image_paths.append(p)
    else:
        raise RuntimeError(f"media_type Instagram non gestito: {info.media_type}")

    # rate-limiting conservativo per non stressare l'account
    time.sleep(min_delay_seconds)

    return DownloadResult(
        folder=folder,
        video_path=video_path,
        image_paths=sorted(image_paths),
        original_caption=original_caption,
    )
