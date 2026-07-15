"""QA tecnico deterministico sugli asset generati (video/immagini).

Solo controlli oggettivi via ffprobe: file esiste, durata, risoluzione,
traccia audio presente. Nessun giudizio creativo qui — quello ("ha senso
per il profilo?") e' uno stadio separato via Claude headless, vedi
claude_creative.py. Stadio deterministico per regola di progetto.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class QAResult:
    passed: bool
    checks: dict
    details: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def _ffprobe_json(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def get_duration_seconds(path: Path) -> float:
    """Durata di un video/audio in secondi. Usata anche per il check di
    idoneita' pre-produzione (video troppo lungo), non solo per il QA
    post-generazione — vedi engine.py."""
    info = _ffprobe_json(path)
    return float(info.get("format", {}).get("duration", 0.0))


def check_video(path: Path, *, min_duration: float = 0.5, require_audio: bool = True) -> QAResult:
    if not path.exists():
        return QAResult(passed=False, checks={"file_exists": False}, errors=[f"File non trovato: {path}"])

    try:
        info = _ffprobe_json(path)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return QAResult(passed=False, checks={"file_exists": True, "ffprobe_ok": False}, errors=[str(exc)])

    duration = float(info.get("format", {}).get("duration", 0.0))
    streams = info.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})

    checks = {
        "file_exists": True,
        "has_video_stream": has_video,
        "duration_ok": duration >= min_duration,
        "has_audio_stream": has_audio if require_audio else True,
    }
    errors = []
    if not has_video:
        errors.append("Nessuna traccia video trovata")
    if duration < min_duration:
        errors.append(f"Durata {duration:.2f}s sotto la soglia minima {min_duration}s")
    if require_audio and not has_audio:
        errors.append("Nessuna traccia audio trovata")

    return QAResult(
        passed=all(checks.values()),
        checks=checks,
        details={"duration": duration, "width": video_stream.get("width"), "height": video_stream.get("height")},
        errors=errors,
    )


def check_image(path: Path, *, min_width: int = 1, min_height: int = 1) -> QAResult:
    if not path.exists():
        return QAResult(passed=False, checks={"file_exists": False}, errors=[f"File non trovato: {path}"])

    try:
        info = _ffprobe_json(path)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return QAResult(passed=False, checks={"file_exists": True, "ffprobe_ok": False}, errors=[str(exc)])

    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    width = video_stream.get("width", 0) or 0
    height = video_stream.get("height", 0) or 0

    checks = {
        "file_exists": True,
        "has_image_stream": bool(video_stream),
        "resolution_ok": width >= min_width and height >= min_height,
    }
    errors = []
    if not video_stream:
        errors.append("Impossibile leggere le dimensioni dell'immagine")
    elif not checks["resolution_ok"]:
        errors.append(f"Risoluzione {width}x{height} sotto la soglia minima {min_width}x{min_height}")

    return QAResult(
        passed=all(checks.values()),
        checks=checks,
        details={"width": width, "height": height},
        errors=errors,
    )
