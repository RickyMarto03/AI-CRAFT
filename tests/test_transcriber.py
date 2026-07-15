"""Test del transcriber sul caso 'video senza traccia audio' (reel muto /
carosello): deve tornare vuoto senza errore, non far fallire il reference.
Genera i video al volo con ffmpeg, nessun asset esterno."""

import subprocess

import pytest

from aicraft.reference_sync import transcriber


@pytest.fixture(autouse=True)
def _skip_if_no_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg non disponibile")


def _video_muto(path, duration=1):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=5", "-c:v", "libx264", str(path)],
        capture_output=True, check=True,
    )


def _video_con_audio(path, duration=1):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=5",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True, check=True,
    )


def test_has_audio_stream_riconosce_video_muto(tmp_path):
    muto = tmp_path / "muto.mp4"
    _video_muto(muto)
    assert transcriber.has_audio_stream(muto) is False


def test_has_audio_stream_riconosce_video_con_audio(tmp_path):
    con_audio = tmp_path / "audio.mp4"
    _video_con_audio(con_audio)
    assert transcriber.has_audio_stream(con_audio) is True


def test_transcribe_video_muto_torna_vuoto_senza_errore(tmp_path):
    muto = tmp_path / "muto.mp4"
    _video_muto(muto)
    transcript, segments, audio_path = transcriber.transcribe_video(muto)
    assert transcript == ""
    assert segments == []
    assert audio_path is None
