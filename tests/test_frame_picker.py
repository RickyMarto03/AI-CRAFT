"""Test dell'algoritmo di scelta frame (priorita' volto su persona anche se
trovata dopo, richiesta di 2 rilevamenti consecutivi per accettare un
volto, fallback quando non si trova nulla).

I rilevatori (_has_face/_has_person) sono mockati: la loro accuratezza
reale (rete DNN vs Haar Cascade, soglie) e' stata verificata a mano contro
contenuto IG reale durante lo sviluppo — vedi le note nel modulo — non e'
riproducibile in modo affidabile in un test automatico senza asset esterni
con volti reali. Qui si testa solo la LOGICA di scelta, indipendente dal
rilevatore usato.
"""

import subprocess

import pytest

from aicraft.production import frame_picker


@pytest.fixture(autouse=True)
def _skip_if_no_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("ffmpeg non disponibile in questo ambiente")


def _make_video(path, duration=3, fps=10):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate={fps}",
            "-c:v", "libx264", str(path),
        ],
        capture_output=True, check=True,
    )


def test_nessun_volto_ne_persona_usa_fallback(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    _make_video(video)
    monkeypatch.setattr(frame_picker, "_has_face", lambda frame: False)
    monkeypatch.setattr(frame_picker, "_has_person", lambda frame: False)

    pick = frame_picker.pick_reference_frame(video, tmp_path / "out.jpg", scan_seconds=1.0, step_seconds=0.3)

    assert pick.method == "fallback"
    assert pick.timestamp_sec == 0.0
    assert pick.frame_path.exists()


def test_hit_isolato_non_basta_serve_conferma_consecutiva(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    _make_video(video, duration=3)
    calls = {"n": 0}

    def fake_has_face(frame):
        calls["n"] += 1
        return calls["n"] == 2  # un solo hit isolato, mai due di fila

    monkeypatch.setattr(frame_picker, "_has_face", fake_has_face)
    monkeypatch.setattr(frame_picker, "_has_person", lambda frame: False)

    pick = frame_picker.pick_reference_frame(video, tmp_path / "out.jpg", scan_seconds=1.0, step_seconds=0.3)

    assert pick.method == "fallback"


def test_due_hit_consecutivi_vengono_accettati_come_volto(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    _make_video(video, duration=3)
    calls = {"n": 0}

    def fake_has_face(frame):
        calls["n"] += 1
        return calls["n"] in (3, 4)  # due hit di fila

    monkeypatch.setattr(frame_picker, "_has_face", fake_has_face)
    monkeypatch.setattr(frame_picker, "_has_person", lambda frame: False)

    pick = frame_picker.pick_reference_frame(video, tmp_path / "out.jpg", scan_seconds=2.0, step_seconds=0.3)

    assert pick.method == "face"


def test_persona_trovata_prima_non_blocca_un_volto_trovato_dopo(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    _make_video(video, duration=3)
    calls = {"n": 0}

    def fake_has_face(frame):
        calls["n"] += 1
        return calls["n"] in (4, 5)  # volto confermato solo verso la fine

    monkeypatch.setattr(frame_picker, "_has_face", fake_has_face)
    monkeypatch.setattr(frame_picker, "_has_person", lambda frame: True)  # persona sempre presente

    pick = frame_picker.pick_reference_frame(video, tmp_path / "out.jpg", scan_seconds=2.0, step_seconds=0.3)

    assert pick.method == "face"  # il volto vince anche se la persona era gia' rilevabile prima


def test_solo_persona_mai_volto_usa_metodo_person(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    _make_video(video, duration=2)
    monkeypatch.setattr(frame_picker, "_has_face", lambda frame: False)
    monkeypatch.setattr(frame_picker, "_has_person", lambda frame: True)

    pick = frame_picker.pick_reference_frame(video, tmp_path / "out.jpg", scan_seconds=1.0, step_seconds=0.3)

    assert pick.method == "person"
    assert pick.timestamp_sec == 0.0  # il primo frame in cui compare la persona


def test_video_inesistente_solleva_errore(tmp_path):
    with pytest.raises(RuntimeError):
        frame_picker.pick_reference_frame(tmp_path / "non_esiste.mp4", tmp_path / "out.jpg")


# ---- sample_frames ----

def test_sample_frames_estrae_il_numero_richiesto(tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video, duration=3, fps=10)  # 30 frame

    paths = frame_picker.sample_frames(video, tmp_path / "frames", count=5)

    assert len(paths) == 5
    assert all(p.exists() for p in paths)


def test_sample_frames_coprono_lintero_video_non_solo_linizio(tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video, duration=3, fps=10)

    paths = frame_picker.sample_frames(video, tmp_path / "frames", count=3)

    # nomi in ordine temporale (analysis_frame_00, 01, 02...), non un
    # singolo frame ripetuto: verifica che siano file distinti
    assert len(set(p.name for p in paths)) == len(paths)


def test_sample_frames_count_maggiore_dei_frame_disponibili_non_esplode(tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video, duration=1, fps=5)  # ~5 frame

    paths = frame_picker.sample_frames(video, tmp_path / "frames", count=100)

    assert 1 <= len(paths) <= 5


def test_sample_frames_video_inesistente_solleva_errore(tmp_path):
    with pytest.raises(RuntimeError):
        frame_picker.sample_frames(tmp_path / "non_esiste.mp4", tmp_path / "frames")
