"""Fonte UNICA di verita' per le operazioni di generazione di ogni content_type.

Sia il Production Engine (che esegue le generazioni) sia il Budget (che le
stima prima dell'approvazione di un piano) leggono da qui: cosi' la stima
costi e la spesa reale usano gli stessi modelli/parametri e non divergono
(esattamente il tipo di disallineamento che CLAUDE.md vieta per i crediti).

`PIPELINE_STAGES` in engine.py resta la sequenza COMPLETA di stadi (incluso
qa/caption/delivery); qui c'e' solo il sottoinsieme di stadi che consuma
crediti Higgsfield, con il modello e i parametri rilevanti per il costo.

Modelli e parametri per il workflow Ruby2 verificati contro l'API reale il
14-15/07/2026 (`higgsfield model get <job_type>`, una generazione reale di
test per kling3_0_motion_control). Vedi docs/ai-craft-architecture.md §12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class GenerationOp:
    stage: str  # "image_regen" | "video_regen" — combacia con PIPELINE_STAGES
    job_type: str  # modello Higgsfield (es. "text2image_soul_v2")
    params: dict = field(default_factory=dict)  # param rilevanti per il costo (duration, quality...)
    count: int = 1  # quante generazioni identiche (es. N immagini di un carosello)
    # Costo fisso da usare quando la stima via `generate cost` non e'
    # disponibile (vedi docs §12.2: l'endpoint di stima per
    # kling3_0_motion_control richiede immagine/video REALI e comunque
    # fallisce con un bug lato CLI — non stimabile in fase di
    # approvazione piano, quando i file veri non esistono ancora).
    manual_cost_estimate: Optional[float] = None

    def params_key(self) -> tuple:
        return (self.job_type, tuple(sorted(self.params.items())))


# NB su `count`: usato SOLO dalla stima di budget (engine.py genera ormai
# un numero dinamico di immagini, pari a len(prompts) da
# claude_creative.write_carousel_prompts — vedi _stage_image_regen). Per
# `carosello`, count=3 e' una stima CONSERVATIVA (il numero massimo che
# carousel_selection.py puo' selezionare): un carosello reale puo' avere
# 1-3 foto a seconda di quante ce ne sono nel post originale, ma prima di
# scaricare la reference non lo sappiamo ancora. Meglio sovrastimare il
# costo in fase di approvazione piano che sottostimarlo — un blocco budget
# di troppo e' un fastidio, un piano approvato che poi costa di piu' del
# saldo e' un problema.
# Aspect ratio dell'immagine Ruby2 (deciso con l'utente, 15/07/2026):
# 1:1 per i post statici (caroselli/stories), 9:16 per il frame estratto
# dai video (talking/balletti/caption) — sono verticali come il video di
# destinazione.
_ASPECT_SQUARE = {"aspect_ratio": "1:1"}
_ASPECT_VERTICAL = {"aspect_ratio": "9:16"}

# Parametri fissi per i video seedance_2_0 (talking/caption), decisi con
# l'utente (15/07/2026, vedi §12.15): 9:16 720p sempre, generate_audio
# acceso perche' il dialogo viene ora scritto per esteso nel prompt (vedi
# claude_creative.write_talking_video_prompt) e deve tradursi in audio
# reale nel video. `generate_audio=true` e' gia' il default reale del
# modello (verificato via `higgsfield model get seedance_2_0`, 15/07/2026),
# esplicitato qui solo per non dipendere da un default upstream che
# potrebbe cambiare.
#
# duration=15 qui e' un WORST CASE per la stima di budget (stesso principio
# del count=3 di carosello sopra: meglio sovrastimare in fase di
# approvazione piano che sottostimare). La generazione REALE in
# engine._stage_video_regen sovrascrive duration con quella vera del video
# originale, che per costruzione e' sempre <= engine.MAX_VIDEO_DURATION_SECONDS
# (15s): un video piu' lungo viene scartato prima con VideoTooLongError.
_SEEDANCE_TALKING_PARAMS = {
    "aspect_ratio": "9:16",
    "resolution": "720p",
    "generate_audio": "true",
    "duration": 15,
}

GENERATION_OPS: dict = {
    "video_talking": [
        GenerationOp("image_regen", "text2image_soul_v2", _ASPECT_VERTICAL),
        GenerationOp("video_regen", "seedance_2_0", _SEEDANCE_TALKING_PARAMS),
    ],
    "video_balletti": [
        GenerationOp("image_regen", "text2image_soul_v2", _ASPECT_VERTICAL),
        # kling3_0_motion_control: nessun duration/aspect_ratio da passare,
        # auto-derivati dal video_references (verificato con job reale
        # 8ddb6b61-..., vedi docs §12.2). manual_cost_estimate = ~16
        # crediti per una clip di ~10s, dato reale fornito dall'utente
        # (15/07/2026) da uso diretto della piattaforma — non ancora
        # verificato con un job nostro completato con successo (il test
        # e' stato bloccato in moderazione prima di generare). Scala
        # presumibilmente con la durata del video originale: per clip
        # molto piu' lunghe/corte questo valore va rivisto.
        GenerationOp("video_regen", "kling3_0_motion_control", {}, manual_cost_estimate=16.0),
    ],
    "video_caption": [
        GenerationOp("image_regen", "text2image_soul_v2", _ASPECT_VERTICAL),
        GenerationOp("video_regen", "seedance_2_0", _SEEDANCE_TALKING_PARAMS),
    ],
    "carosello": [
        GenerationOp("image_regen", "text2image_soul_v2", _ASPECT_SQUARE, count=3),
    ],
    # "stories" non specificato dall'utente (ha dato solo caroselli->1:1 e
    # video->9:16): assunto 9:16 perche' le Instagram Stories sono
    # verticali a schermo intero per convenzione della piattaforma, non
    # quadrate come i post. Da correggere se sbagliato.
    "stories": [
        GenerationOp("image_regen", "text2image_soul_v2", _ASPECT_VERTICAL),
    ],
}


def generation_ops(content_type: str) -> list:
    if content_type not in GENERATION_OPS:
        raise KeyError(f"content_type sconosciuto in GENERATION_OPS: {content_type!r}")
    return GENERATION_OPS[content_type]


def image_op(content_type: str) -> GenerationOp:
    return next(op for op in generation_ops(content_type) if op.stage == "image_regen")


def video_op(content_type: str) -> GenerationOp:
    return next(op for op in generation_ops(content_type) if op.stage == "video_regen")
