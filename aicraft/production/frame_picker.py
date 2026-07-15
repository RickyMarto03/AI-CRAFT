"""Trova il primo frame di un video in cui e' visibile la ragazza, per
generare la foto-base Ruby2 (usata sia per balletti sia per talking). Stadio
deterministico (codice puro, nessun giudizio creativo, coerente con la
regola di progetto): due livelli di rilevamento in cascata.

Livelli (in ordine):
1. Volto (rete neurale, SSD ResNet10 su Caffe, modello standard di OpenCV) —
   affidabile anche su inquadrature parziali o leggermente angolate (mezzo
   busto, solo viso), non solo frontali pure.
2. Persona generica (HOG people detector) — copre il caso "di spalle
   all'inizio", dove non c'e' nessun volto rilevabile.
3. Fallback fisso (primo frame scansionato) se nessun rilevatore trova
   nulla entro la finestra di scan — esito marcato esplicitamente come
   'fallback' cosi' resta tracciabile e non si confonde con un rilevamento
   vero.

STORIA — perche' non Haar Cascade: il primo tentativo usava i classici
Haar Cascade (frontale + profilo) di OpenCV, con una verifica incrociata
tramite rilevatore di occhi per ridurre i falsi positivi. Testato su
contenuto reale: un frame con la ragazza ripresa di spalle veniva
segnalato come "volto frontale" (falso positivo dovuto a texture di
capelli), e tarare la sensibilita' della verifica occhi per escludere quel
caso finiva per far perdere anche volti veri (falsi negativi) — i due
errori non si bilanciavano bene con nessuna combinazione di soglie
provata. Il rilevatore DNN, verificato sugli stessi casi reali, classifica
correttamente entrambi senza questo compromesso.

I file dei modelli (Haar cascade eye/profile per compatibilita' storica,
+ i pesi DNN) sono scaricati dal repo ufficiale OpenCV e versionati in
aicraft/production/{cascades,dnn_models}/: opencv-python 5.x (verificato
15/07/2026) non include piu' i Haar cascade nel pacchetto, e dipendere dal
percorso interno del pacchetto installato sarebbe fragile tra versioni.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_DNN_DIR = Path(__file__).resolve().parent / "dnn_models"
_DNN_PROTOTXT = _DNN_DIR / "deploy.prototxt"
_DNN_WEIGHTS = _DNN_DIR / "res10_300x300_ssd_iter_140000.caffemodel"
_FACE_CONFIDENCE_THRESHOLD = 0.5

_face_net = None
_hog_detector = None


def _get_face_net():
    global _face_net
    if _face_net is None:
        _face_net = cv2.dnn.readNetFromCaffe(str(_DNN_PROTOTXT), str(_DNN_WEIGHTS))
    return _face_net


def _get_hog_detector():
    global _hog_detector
    if _hog_detector is None:
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        _hog_detector = hog
    return _hog_detector


@dataclass
class FramePick:
    frame_path: Path
    timestamp_sec: float
    method: str  # "face" | "person" | "fallback"


def _has_face(bgr) -> bool:
    net = _get_face_net()
    blob = cv2.dnn.blobFromImage(cv2.resize(bgr, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
    net.setInput(blob)
    detections = net.forward()
    confidences = detections[0, 0, :, 2]
    return bool(np.any(confidences > _FACE_CONFIDENCE_THRESHOLD))


def _has_person(bgr) -> bool:
    boxes, _weights = _get_hog_detector().detectMultiScale(bgr, winStride=(8, 8))
    return len(boxes) > 0


def pick_reference_frame(
    video_path: Path,
    output_path: Path,
    *,
    scan_seconds: float = 6.0,
    step_seconds: float = 0.4,
) -> FramePick:
    """Scandisce i primi `scan_seconds` del video a passi di `step_seconds`,
    prova i due livelli di rilevamento in ordine su ogni frame, salva come
    JPEG il primo frame utile in `output_path`.
    """
    # Un volto trovato piu' avanti nella finestra vale sempre piu' di una
    # "persona generica" trovata prima: si scandisce l'INTERA finestra
    # cercando un volto ovunque, e solo se non se ne trova nessuno si
    # ripiega sul primo frame con una persona (es. inquadratura di spalle
    # per tutta la finestra). Senza questo, un hit "person" al primo frame
    # bloccherebbe la ricerca anche se il volto appare un istante dopo.
    #
    # Un singolo frame con confidenza alta pero' NON basta: verificato su
    # contenuto reale che il rilevatore DNN puo' avere un picco isolato di
    # falso positivo su un singolo frame (es. capelli in movimento con
    # motion blur, confidenza anche >0.9) mentre un volto vero resta
    # rilevato su piu' campionamenti consecutivi di fila. Si richiedono
    # quindi 2 rilevamenti consecutivi prima di accettare un volto.
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Impossibile aprire il video: {video_path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step_frames = max(1, round(step_seconds * fps))
        max_frames = round(scan_seconds * fps)

        first_frame_seen = None  # per il fallback finale
        first_person_seen = None  # per il ripiego se nessun volto nella finestra
        consecutive_face_hits = 0
        frame_idx = 0

        while frame_idx < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                break

            timestamp = frame_idx / fps
            if first_frame_seen is None:
                first_frame_seen = (frame, timestamp)

            if _has_face(frame):
                consecutive_face_hits += 1
                if consecutive_face_hits >= 2:
                    return _save(frame, timestamp, output_path, "face")
            else:
                consecutive_face_hits = 0
                if first_person_seen is None and _has_person(frame):
                    first_person_seen = (frame, timestamp)

            frame_idx += step_frames
    finally:
        cap.release()

    if first_person_seen is not None:
        frame, timestamp = first_person_seen
        return _save(frame, timestamp, output_path, "person")

    if first_frame_seen is None:
        raise RuntimeError(f"Nessun frame leggibile nei primi {scan_seconds}s di {video_path}")

    frame, timestamp = first_frame_seen
    return _save(frame, timestamp, output_path, "fallback")


def _save(frame, timestamp: float, output_path: Path, method: str) -> FramePick:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), frame)
    return FramePick(frame_path=output_path, timestamp_sec=timestamp, method=method)


def sample_frames(video_path: Path, output_dir: Path, *, count: int = 5) -> list:
    """Estrae `count` frame equispaziati lungo l'INTERO video (a differenza
    di `pick_reference_frame`, che guarda solo la finestra iniziale per
    trovare la foto-base del personaggio). Serve per l'analisi visiva di
    movimenti/outfit/background nel tempo dei video talking/caption (vedi
    claude_creative.write_talking_video_prompt e
    docs/ai-craft-architecture.md §12.15). Ritorna i path in ordine
    temporale, come JPEG in output_dir."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Impossibile aprire il video: {video_path}")

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            raise RuntimeError(f"Video senza frame leggibili: {video_path}")

        output_dir.mkdir(parents=True, exist_ok=True)
        n = min(count, total_frames)
        paths = []
        for i in range(n):
            frame_idx = round(i * (total_frames - 1) / (n - 1)) if n > 1 else 0
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                continue
            out_path = output_dir / f"analysis_frame_{i:02d}.jpg"
            cv2.imwrite(str(out_path), frame)
            paths.append(out_path)

        if not paths:
            raise RuntimeError(f"Nessun frame estratto da {video_path}")
        return paths
    finally:
        cap.release()
