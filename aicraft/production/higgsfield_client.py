"""Wrapper Python attorno al CLI ufficiale Higgsfield (`higgsfield`), non MCP.

Decisione presa in docs/ai-craft-architecture.md §7: il Production Engine e'
orchestrato in modo deterministico da Python, non e' una sessione agente;
l'MCP di Higgsfield e' pensato per un agente che sceglie modello/parametri
in linguaggio naturale dentro una chat, il che romperebbe la separazione
creativo/deterministico e il tracciamento costi via CreditLedger.

STATO: sintassi comandi e schema JSON VERIFICATI il 14/07/2026 contro un
account reale gia' autenticato sulla macchina (`higgsfield account status`),
con una generazione immagine reale di test (text2image_soul_v2, 0.12
crediti). Il ramo video (kling3_0) e' stato verificato solo per i parametri
accettati (`higgsfield model get kling3_0`) e il costo stimato (10 crediti),
non con una generazione reale — costo/tempo non giustificati per una verifica
di schema che il ramo immagine ha gia' confermato. Vedi
docs/ai-craft-architecture.md §7 per i dettagli.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .. import config


class HiggsfieldError(RuntimeError):
    pass


class HiggsfieldNSFWBlockedError(HiggsfieldError):
    """Il job e' stato bloccato dalla moderazione content (status 'nsfw').

    Non e' un errore transitorio: un retry con lo stesso input fallirebbe
    di nuovo allo stesso modo, va trattato come esito distinto da un
    fallimento generico. Verificato che non consuma crediti (job di test
    reale, saldo invariato prima/dopo). Vedi docs/ai-craft-architecture.md
    §12.2.
    """

    pass


@dataclass
class GenerationResult:
    job_id: str
    status: str
    result_url: Optional[str]
    cost_credits: Optional[float]
    raw: dict


def _run_json_raw(args: list):
    """Esegue un comando `higgsfield ... --json` e ritorna il JSON grezzo."""
    cmd = [config.HIGGSFIELD_CLI_BIN, *args, "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise HiggsfieldError(
            f"Binario '{config.HIGGSFIELD_CLI_BIN}' non trovato. "
            "Installare con `npm install -g @higgsfield/cli` ed eseguire `higgsfield auth login`."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HiggsfieldError(f"Comando fallito ({' '.join(cmd)}): {exc.stderr.strip()}") from exc

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise HiggsfieldError(f"Output non JSON da `{' '.join(cmd)}`: {proc.stdout[:300]!r}") from exc


def _run_json(args: list) -> dict:
    data = _run_json_raw(args)

    if isinstance(data, list):
        if not data:
            raise HiggsfieldError(f"Lista di job vuota da `{' '.join([config.HIGGSFIELD_CLI_BIN, *args, '--json'])}`")
        return data[0]
    return data


def _run_create_json_with_reconcile(args: list, model: str) -> dict:
    try:
        return _run_json(args)
    except HiggsfieldError as exc:
        recovered = reconcile_recent_job(model)
        if recovered is not None and recovered.result_url:
            return recovered.raw
        raise exc


def _parse_generation_result(data: dict) -> GenerationResult:
    job_id = data.get("id") or data.get("job_id")
    status = data.get("status", "unknown")
    result_url = data.get("result_url") or data.get("url")
    # Il costo per-job NON e' incluso nella risposta di `generate create`/`get`
    # (verificato: solo id/status/result_url/params/...). Va richiesto a
    # parte con `generate cost` prima di lanciare il job — vedi estimate_cost.
    cost_credits = data.get("cost_credits") or data.get("credits") or data.get("cost")
    if job_id is None:
        raise HiggsfieldError(f"Risposta senza job id riconoscibile: {data}")
    return GenerationResult(
        job_id=str(job_id),
        status=str(status),
        result_url=result_url,
        cost_credits=float(cost_credits) if cost_credits is not None else None,
        raw=data,
    )


def estimate_cost(job_type: str, *, extra_args: Optional[list] = None, **params: str) -> Optional[float]:
    """`higgsfield generate cost <job_type> --param value...` — non crea un job.

    I flag usano il nome del parametro COSI' COM'E' (underscore, es.
    --aspect_ratio, --custom_reference_id, --background_source) — verificato
    contro l'API reale il 14-15/07/2026. Solo i flag "media" (--image-references
    e simili, usati in generate_image/generate_video/generate_motion_control,
    non qui) sono un'eccezione con il trattino. Prima convertiva erroneamente
    ogni underscore in trattino, il che avrebbe rotto la stima per qualunque
    param multi-parola (es. aspect_ratio) non appena ne avessimo passato uno —
    bug mai emerso finora perche' gli unici param passati fin qui (prompt,
    duration) sono parole singole.
    """
    args = ["generate", "cost", job_type]
    for key, value in params.items():
        args += [f"--{key}", str(value)]
    if extra_args:
        args += extra_args
    data = _run_json(args)
    cost = data.get("credits") or data.get("cost_credits") or data.get("cost")
    return float(cost) if cost is not None else None


def generate_image(
    prompt: str,
    *,
    model: str = "text2image_soul_v2",
    aspect_ratio: Optional[str] = None,
    image_references: Optional[list] = None,
    custom_reference_id: Optional[str] = None,
    extra_args: Optional[list] = None,
) -> GenerationResult:
    args = ["generate", "create", model, "--prompt", prompt, "--wait"]
    if aspect_ratio:
        args += ["--aspect_ratio", aspect_ratio]
    for ref in image_references or []:
        args += ["--image-references", ref]
    if custom_reference_id:
        # personaggio Soul (es. Ruby2) da applicare all'immagine — vedi
        # character.py e docs/ai-craft-architecture.md §12.2.
        args += ["--custom_reference_id", custom_reference_id]
    if extra_args:
        args += extra_args
    return _parse_generation_result(_run_create_json_with_reconcile(args, model))


def generate_video(
    prompt: str,
    *,
    model: str = "kling3_0",
    start_image: Optional[str] = None,
    duration: Optional[int] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    generate_audio: Optional[str] = None,
    video_references: Optional[list] = None,
    extra_args: Optional[list] = None,
) -> GenerationResult:
    """aspect_ratio/resolution/generate_audio/video_references aggiunti per
    seedance_2_0 (video_talking/video_caption, vedi pipeline_spec.py e
    docs/ai-craft-architecture.md §12.15): sintassi CLI verificata solo via
    `higgsfield model get seedance_2_0` (lookup gratuito), NON con una
    generazione reale — video_references in particolare non e' mai stato
    testato con un job pagato, va verificato al primo giro reale (vedi
    aicraft/production/settings.py per il flag che lo attiva)."""
    args = ["generate", "create", model, "--prompt", prompt, "--wait"]
    if start_image:
        args += ["--start-image", start_image]
    if duration:
        args += ["--duration", str(duration)]
    if aspect_ratio:
        args += ["--aspect_ratio", aspect_ratio]
    if resolution:
        args += ["--resolution", resolution]
    if generate_audio is not None:
        args += ["--generate_audio", generate_audio]
    for ref in video_references or []:
        args += ["--video-references", ref]
    if extra_args:
        args += extra_args
    return _parse_generation_result(_run_create_json_with_reconcile(args, model))


def generate_motion_control(
    image_reference: str,
    video_reference: str,
    *,
    model: str = "kling3_0_motion_control",
    mode: str = "std",
    background_source: str = "input_video",
) -> GenerationResult:
    """`kling3_0_motion_control`: trasferisce il movimento di un video su
    una foto — nessun prompt (verificato con job reale il 15/07/2026:
    "prompt": null nell'output), niente duration/aspect_ratio da passare
    (auto-derivati dal video). Convenzione di chiamata diversa da
    generate_video: image_references/video_references, non start_image.
    Vedi docs/ai-craft-architecture.md §12.2.
    """
    args = [
        "generate", "create", model,
        "--image-references", image_reference,
        "--video-references", video_reference,
        "--background_source", background_source,
        "--mode", mode,
        "--wait",
    ]
    try:
        return _parse_generation_result(_run_create_json_with_reconcile(args, model))
    except HiggsfieldError as exc:
        if "nsfw" in str(exc).lower():
            raise HiggsfieldNSFWBlockedError(str(exc)) from exc
        raise


def download_result(url: str, dest_path: Path) -> Path:
    """Scarica in locale un `result_url` di un job Higgsfield (URL CDN
    remoto). GAP REALE trovato in review (15/07/2026): prima di questo fix,
    `generated_assets` teneva SOLO l'URL remoto per tutta la pipeline —
    `qa.check_image`/`check_video` fanno `Path(url).exists()` che per un URL
    e' SEMPRE False, quindi il QA sarebbe fallito su qualunque asset reale
    mai generato finora (mai emerso perche' nessun test/uso reale era mai
    arrivato fino a un QA su un asset Higgsfield vero — vedi
    docs/ai-craft-architecture.md §16).

    Se `url` NON e' un URL http(s) (es. un path locale, usato nei test o in
    scenari futuri in cui un job restituisse gia' un file locale), ritorna
    il path cosi' com'e' senza fare alcuna richiesta di rete — cosi' i test
    esistenti che passano path locali finti come "result_url" continuano a
    funzionare senza dover mockare anche questa funzione.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        return Path(url)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    dest_path.write_bytes(resp.content)
    return dest_path


def get_job(job_id: str) -> GenerationResult:
    return _parse_generation_result(_run_json(["generate", "get", job_id]))


def list_recent_jobs(limit: int = 20) -> list[GenerationResult]:
    data = _run_json_raw(["generate", "list"])
    rows = data if isinstance(data, list) else data.get("jobs", [])
    results = []
    for row in rows[:limit]:
        try:
            results.append(_parse_generation_result(row))
        except HiggsfieldError:
            continue
    return results


def reconcile_recent_job(model: str, *, limit: int = 20) -> GenerationResult | None:
    """Recupera un job recente completato dopo un errore di `--wait`.

    In alcuni casi il provider puo' aver creato/completato il job e il CLI
    fallire solo durante l'attesa. Prima di arrenderci guardiamo la lista
    recente e cerchiamo un job dello stesso modello con risultato.
    """
    try:
        jobs = list_recent_jobs(limit=limit)
    except HiggsfieldError:
        return None
    for job in jobs:
        raw_model = job.raw.get("model") or job.raw.get("job_type") or job.raw.get("type")
        if raw_model == model and job.status in {"completed", "succeeded", "success"} and job.result_url:
            return job
    return None


def account_status() -> dict:
    """`higgsfield account status --json` -> {"credits": float, "email": str,
    "subscription_plan_type": str}. Schema verificato contro l'account reale."""
    return _run_json(["account", "status"])
