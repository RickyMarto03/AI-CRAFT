"""Trascrizione locale via Whisper (faster-whisper).

Claude non ha input audio nativo (verificato sui docs ufficiali, luglio
2026: tutti i modelli Claude correnti supportano solo testo e immagini),
quindi la trascrizione vera e propria passa da Whisper. Claude headless
resta riservato agli stadi creativi a valle, che useranno il testo
prodotto qui come input. Vedi docs/ai-craft-architecture.md §7.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .. import config

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(config.WHISPER_MODEL_SIZE, device="auto", compute_type="int8")
    return _model


def has_audio_stream(video_path: Path) -> bool:
    """True se il video ha almeno una traccia audio. Molti reel/caroselli
    (balletti, foto animate) non ce l'hanno: senza questo controllo ffmpeg
    fallirebbe e l'intero reference verrebbe marcato erroneamente 'error'."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "json", str(video_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(proc.stdout).get("streams", [])
    return len(streams) > 0


def extract_audio(video_path: Path) -> Path:
    """Estrae l'audio mono 16kHz dal video, formato atteso da Whisper. Richiede ffmpeg nel PATH."""
    audio_path = video_path.with_suffix(".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-ac", "1", "-ar", "16000", str(audio_path)],
        check=True,
        capture_output=True,
    )
    return audio_path


def transcribe(audio_path: Path) -> tuple:
    """Ritorna (transcript_piatto, segmenti). Ogni segmento e' un dict
    {"start": float, "end": float, "text": str} — faster-whisper li produce
    gia' nativamente, prima venivano scartati unendo solo il testo. Servono
    per correlare con precisione dialogo e frame video (vedi
    claude_creative.write_talking_video_prompt e docs §12.15/§12.16): senza
    timestamp, sapere quale frase corrisponde a quale momento del video e'
    solo un'approssimazione a occhio."""
    model = _get_model()
    raw_segments, _info = model.transcribe(str(audio_path), language=None)
    segments = [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in raw_segments
    ]
    transcript = " ".join(s["text"] for s in segments).strip()
    return transcript, segments


def transcribe_video(video_path: Path) -> tuple:
    """Trascrive un video end-to-end. Ritorna (transcript, segments, audio_path).

    Se il video non ha traccia audio, ritorna ("", [], None) senza errore:
    un video muto e' un caso legittimo, non un fallimento.
    """
    if not has_audio_stream(video_path):
        return "", [], None
    audio_path = extract_audio(video_path)
    transcript, segments = transcribe(audio_path)
    return transcript, segments, audio_path
