"""Test del QA tecnico contro file veri, generati al volo con ffmpeg
(nessun asset esterno necessario, nessuna credenziale)."""

import subprocess

import pytest

from aicraft.production import qa


def _make_video_with_audio(path, duration=2):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True, check=True,
    )


def _make_video_without_audio(path, duration=2):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-c:v", "libx264", str(path),
        ],
        capture_output=True, check=True,
    )


def _make_image(path, width=640, height=480):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:duration=1:rate=1", "-frames:v", "1", str(path)],
        capture_output=True, check=True,
    )


@pytest.fixture(scope="module", autouse=True)
def _skip_if_no_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg non disponibile in questo ambiente")


def test_check_video_file_inesistente(tmp_path):
    result = qa.check_video(tmp_path / "non_esiste.mp4")
    assert result.passed is False
    assert result.checks["file_exists"] is False


def test_check_video_con_audio_passa(tmp_path):
    video_path = tmp_path / "ok.mp4"
    _make_video_with_audio(video_path)

    result = qa.check_video(video_path, min_duration=0.5, require_audio=True)

    assert result.passed is True
    assert result.checks["has_video_stream"] is True
    assert result.checks["has_audio_stream"] is True
    assert result.details["duration"] >= 1.5


def test_check_video_senza_audio_fallisce_se_richiesto(tmp_path):
    video_path = tmp_path / "no_audio.mp4"
    _make_video_without_audio(video_path)

    result = qa.check_video(video_path, min_duration=0.5, require_audio=True)

    assert result.passed is False
    assert result.checks["has_audio_stream"] is False
    assert any("audio" in e.lower() for e in result.errors)


def test_check_video_senza_audio_passa_se_non_richiesto(tmp_path):
    video_path = tmp_path / "no_audio.mp4"
    _make_video_without_audio(video_path)

    result = qa.check_video(video_path, min_duration=0.5, require_audio=False)

    assert result.passed is True


def test_check_video_durata_insufficiente(tmp_path):
    video_path = tmp_path / "corto.mp4"
    _make_video_with_audio(video_path, duration=1)

    result = qa.check_video(video_path, min_duration=5.0, require_audio=True)

    assert result.passed is False
    assert result.checks["duration_ok"] is False


def test_check_image_passa(tmp_path):
    image_path = tmp_path / "img.jpg"
    _make_image(image_path, width=640, height=480)

    result = qa.check_image(image_path, min_width=100, min_height=100)

    assert result.passed is True
    assert result.details["width"] == 640
    assert result.details["height"] == 480


def test_check_image_risoluzione_insufficiente(tmp_path):
    image_path = tmp_path / "img_piccola.jpg"
    _make_image(image_path, width=100, height=100)

    result = qa.check_image(image_path, min_width=1000, min_height=1000)

    assert result.passed is False
    assert result.checks["resolution_ok"] is False
