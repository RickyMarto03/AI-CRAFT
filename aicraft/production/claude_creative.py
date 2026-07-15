"""Wrapper per invocare Claude Code in modalita' headless (`claude -p`) per
gli stadi creativi del Production Engine (prompt di rigenerazione, caption,
hashtag, prompt-writing carosello con vision). Stadi deterministici
(download, QA tecnico, delivery, naming) restano codice puro per regola di
progetto — qui passa SOLO cio' che richiede giudizio creativo/linguistico.

STATO: verificato con chiamate reali (non solo mockate), incluso vision su
foto vere via `--allowedTools Read` — vedi docs/ai-craft-architecture.md
§12. Il binario `claude` va installato separatamente e messo in PATH (non
e' detto sia gia' lì solo perche' si usa l'app/estensione Claude Code).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Optional

from .. import config

logger = logging.getLogger(__name__)

_MARKDOWN_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)

# Aggiunta al prompt SOLO quando si ritenta un pezzo che era stato rifiutato
# da Claude per policy di contenuto (ContentPiece.was_refused, vedi
# engine.retry_content_piece) — richiesto dall'utente (15/07/2026) invece di
# ripetere lo stesso identico input, che darebbe di nuovo lo stesso rifiuto.
_REFUSAL_RETRY_CLAUSE = (
    "IMPORTANT: a previous attempt at this same content was refused by content policy. "
    "Reformulate more conservatively this time: avoid explicit or extreme close-up sexualized "
    "descriptions, keep clothing/poses/expressions tasteful and general rather than explicit, "
    "while still following the structure and constraints above.\n\n"
)


def _strip_markdown_fence(text: str) -> str:
    """Claude a volte avvolge il JSON in un blocco markdown ```json ... ```
    nonostante l'istruzione esplicita di non farlo (verificato con una
    chiamata reale, 15/07/2026) — lo stacca prima del parsing invece di
    fallire."""
    match = _MARKDOWN_FENCE_RE.match(text.strip())
    return match.group(1).strip() if match else text

# Schema fisso per lo stadio caption_hashtag — non improvvisato dal prompt
# a runtime, vedi docs/ai-craft-architecture.md §7.
CAPTION_HASHTAG_SCHEMA_HINT = (
    '{"caption": "...", "hashtags": ["#tag1", "#tag2"]}'
)

# Target di lunghezza del prompt FINALE (descrizione fisica fissa + scena
# scritta da Claude + modificatori obbligatori), deciso con l'utente. Vedi
# docs/ai-craft-architecture.md §12.
TARGET_PROMPT_LEN_MIN = 2200
TARGET_PROMPT_LEN_MAX = 2400
_MAX_SCENE_RETRIES = 2


class ClaudeCreativeError(RuntimeError):
    pass


class ClaudeContentRefusedError(ClaudeCreativeError):
    """Claude ha rifiutato di generare il prompt per policy di contenuto
    (osservato la prima volta il 15/07/2026 su una foto ravvicinata
    sessualizzata di persona reale, vedi docs/ai-craft-architecture.md §12.8
    e §16). Non e' un errore tecnico e non e' recuperabile con un retry
    sullo stesso input — stesso principio di HiggsfieldNSFWBlockedError e
    VideoTooLongError: un esito legittimo, distinto da un fallimento
    generico, va marcato con uno stato dedicato invece di "error".

    Rilevamento euristico (`_looks_like_refusal`): il CLI headless non
    espone un modo strutturato per sapere se la risposta e' un rifiuto —
    si riconosce dal testo, come per "nsfw" negli errori Higgsfield.
    """

    pass


_REFUSAL_PATTERNS = (
    "i can't help", "i cannot help",
    "i can't create", "i cannot create",
    "i can't assist", "i cannot assist",
    "i can't provide", "i cannot provide",
    "i can't write", "i cannot write",
    "i can't generate", "i cannot generate",
    "i'm not able to", "i am not able to",
    "i won't be able to", "i will not be able to",
    "i don't feel comfortable", "i do not feel comfortable",
    "against my guidelines", "against these guidelines",
    "i'm not comfortable", "i am not comfortable",
    "non posso aiutarti", "non posso generare", "non posso creare",
    "non posso procedere", "non posso scrivere", "mi dispiace, non posso",
)


def _looks_like_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _REFUSAL_PATTERNS)


def run_headless(prompt: str, *, allowed_tools: Optional[list] = None, system_prompt: Optional[str] = None) -> str:
    """Esegue `claude -p <prompt>` in modalita' headless, ritorna il testo di risposta."""
    cmd = [config.CLAUDE_CLI_BIN, "-p", prompt, "--output-format", "json"]
    if allowed_tools is not None:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise ClaudeCreativeError(
            f"Binario '{config.CLAUDE_CLI_BIN}' non trovato nel PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ClaudeCreativeError(f"Comando claude fallito: {exc.stderr.strip()}") from exc

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCreativeError(f"Output non JSON da claude -p: {proc.stdout[:300]!r}") from exc

    result = data.get("result")
    if result is None:
        raise ClaudeCreativeError(f"Risposta senza campo 'result': {data}")
    return result


def write_talking_video_prompt(
    *,
    frames: list,
    transcript: str,
    character,
    content_type: str,
    source_category: str,
    duration_seconds: float,
    use_video_reference: bool,
    transcript_segments: Optional[list] = None,
    avoid_refusal: bool = False,
) -> str:
    """Prompt cinematografico completo per seedance_2_0 (video_talking/
    video_caption). Sostituisce il vecchio `write_regen_prompt` (troppo
    generico, nessuna vision, nessuna struttura) — deciso con l'utente
    (15/07/2026) dopo aver visto due prompt reali funzionanti sulla stessa
    piattaforma (vedi docs/ai-craft-architecture.md §12.15).

    Il dialogo viene dalla trascrizione Whisper del video originale, non
    inventato da Claude: puo' solo ripulire refusi di trascrizione (istruito
    esplicitamente sotto), MAI cambiare significato/ordine/aggiungere frasi
    — stesso principio della descrizione fisica del personaggio, che non e'
    mai lasciata alla memoria di Claude (vedi character.py). Claude guarda
    i frame campionati lungo l'INTERO video (frame_picker.sample_frames,
    non solo l'inquadratura iniziale) per dedurre movimenti/camera/pacing.

    `frames`: lista di oggetti con `.path` e `.timestamp_sec` (vedi
    frame_picker.SampledFrame). `transcript_segments`: lista opzionale di
    {"start", "end", "text"} (vedi transcriber.transcribe) — quando presente,
    permette a Claude di correlare dialogo e frame PER SECONDO ESATTO invece
    di indovinare l'allineamento guardando solo la sequenza di immagini
    (limite di precisione segnalato dall'utente il 15/07/2026, vedi §12.16).
    Se assente (reference scaricate prima di questa modifica), il prompt
    degrada al comportamento precedente basato solo sull'ordine dei frame.

    Output: testo libero strutturato (non JSON, a differenza di
    write_carousel_prompts) — lo stesso formato dei prompt reali forniti
    dall'utente come esempio.
    """
    if not frames:
        raise ValueError("servono i frame del video per l'analisi")
    if not transcript.strip():
        raise ValueError("serve la trascrizione per scrivere il dialogo")

    paths_list = "\n".join(f"- [{f.timestamp_sec:.1f}s] {f.path}" for f in frames)

    if transcript_segments:
        segments_list = "\n".join(
            f"- [{s['start']:.1f}s–{s['end']:.1f}s] ‘{s['text'].strip()}’" for s in transcript_segments
        )
        transcript_block = (
            f"Transcript segments WITH EXACT TIMESTAMPS (use these seconds to figure out which frame "
            f"corresponds to which line — don't guess the alignment, the numbers above the frames and "
            f"below here are on the same time scale as the original video):\n{segments_list}\n\n"
            f"Full transcript as a single block, for reference:\n\"\"\"\n{transcript.strip()}\n\"\"\""
        )
    else:
        transcript_block = (
            f"This is the EXACT transcript of what the person says in the video (no per-segment "
            f"timestamps available: infer the alignment with the frames from order and content):\n\"\"\"\n"
            f"{transcript.strip()}\n\"\"\""
        )

    if use_video_reference:
        reference_clause = (
            "The original video will be passed to the model ONLY as a reference for movement/framing/"
            "camera pacing — it must NOT influence physical appearance, outfit, colors, or identity: "
            "those come ONLY from the reference photo provided separately. Write the REFERENCE USAGE "
            "section stating this explicitly: identity/outfit/appearance from the reference photo, "
            "movement/framing from the reference video, without redesigning the character or the setting."
        )
    else:
        reference_clause = (
            "No video reference is passed to the model, only the photo. Write the REFERENCE USAGE "
            "section stating that identity/outfit/setting come from the reference photo, and YOU "
            "describe in words, precisely, every camera/body/hand/head movement observed in the "
            "frames: it's the only way the model will know how to move."
        )

    prompt = (
        f"Carefully look at these {len(frames)} frames sampled across the entire original video, each "
        f"labeled with the exact second it appears at in the video (use the file-reading tool, they are "
        f"images):\n{paths_list}\n\n"
        f"This is an Instagram video of type '{content_type}' (category '{source_category}'), original "
        f"duration {duration_seconds:.1f} seconds. {transcript_block}\n\n"
        "Write ONE complete cinematic prompt to regenerate this video with a new model, following this "
        "structure (same format as real prompts already used successfully on this platform):\n\n"
        f"REFERENCE USAGE: {reference_clause}\n\n"
        "STYLE: overall visual style observed in the frames (e.g. realistic smartphone selfie, framing, "
        "environment/lighting).\n\n"
        "ACTION/PERFORMANCE: describe the body/hand/head/gaze movements and facial expressions observed "
        "in the frames, tying them to the EXACT DIALOGUE LINES (quote the line in single quotes, then "
        "describe the movement that accompanies it) — use the frame and segment timestamps to precisely "
        "match which line is said at which moment/frame, instead of guessing from order. Follow the "
        "dialogue order from start to finish.\n\n"
        "CAMERA: camera position/movement observed in the frames (e.g. fixed smartphone camera, slight "
        "handheld movement, no cuts if the frames show continuity).\n\n"
        f"PACING: pacing to fit within {duration_seconds:.1f} seconds total, explicitly state the "
        "duration in the text (e.g. 'Create a X-second single continuous take...').\n\n"
        "DIALOGUE AND AUDIO: report the dialogue in single quotes in the exact order of the transcript "
        "provided above — you may correct ONLY obvious automatic-transcription glitches (mistaken "
        "repetitions, isolated filler sounds), you may NOT change the meaning, the order, or add lines "
        "that aren't in the transcript. Specify vocal tone and delivery pace.\n\n"
        "CONSTRAINTS: no editing cuts (single continuous shot) unless the frames clearly show otherwise, "
        "no unnatural zooms, no on-screen text/subtitles/logos/watermarks, no background music, no "
        "dialogue other than what was provided, no lip-sync errors.\n\n"
        "Write densely and concretely (precise visual facts, not atmospheric prose). Do NOT include "
        "physical descriptions of the person (face, body, hair, skin color, outfit): those are added "
        "separately in code, you only write the sections above.\n\n"
        "IMPORTANT for formatting: if you need to quote text/writing visible in the frames or in the "
        "dialogue, use single quotes 'like this', never double quotes.\n\n"
        f"{_REFUSAL_RETRY_CLAUSE if avoid_refusal else ''}"
        "Reply ONLY with the final prompt text (the sections above, with uppercase headings as in the "
        "real examples), no comments, no markdown."
    )

    scene = _strip_markdown_fence(run_headless(prompt, allowed_tools=["Read"]).strip())
    if _looks_like_refusal(scene):
        raise ClaudeContentRefusedError(f"Claude ha rifiutato di generare il prompt video: {scene[:300]!r}")
    if not scene:
        raise ClaudeCreativeError("Prompt video talking vuoto")

    return _assemble_full_prompt(character, scene)


def write_caption_and_hashtags(*, transcript: str, content_type: str) -> dict:
    """Ritorna {"caption": str, "hashtags": list[str]}."""
    prompt = (
        f"Scrivi una caption Instagram e una lista di hashtag per un contenuto "
        f"di tipo '{content_type}', basandoti su questa trascrizione:\n\n{transcript}\n\n"
        f"Rispondi SOLO con un JSON valido in questa forma esatta: "
        f"{CAPTION_HASHTAG_SCHEMA_HINT}. Nessun altro testo, nessun markdown."
    )
    raw = _strip_markdown_fence(run_headless(prompt, allowed_tools=[]).strip())
    if _looks_like_refusal(raw):
        raise ClaudeContentRefusedError(f"Claude ha rifiutato di scrivere la caption: {raw[:300]!r}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeCreativeError(f"Caption/hashtag non nel formato JSON atteso: {raw[:300]!r}") from exc

    if "caption" not in data or "hashtags" not in data:
        raise ClaudeCreativeError(f"JSON caption/hashtag incompleto: {data}")
    return {"caption": str(data["caption"]), "hashtags": [str(h) for h in data["hashtags"]]}


def adapt_original_caption_and_hashtags(*, original_caption: str, transcript: str = "", content_type: str) -> dict:
    """Adatta una caption Instagram sorgente invece di inventarla da zero.

    Il workflow concordato con l'utente prevede che caption/hashtag siano
    copiati/adattati dal post originale quando disponibili. Claude puo'
    ripulire mention/link/testi incoerenti col nuovo contenuto, ma non deve
    cambiare tono o inventare un angolo editoriale nuovo.
    """
    prompt = (
        f"Adatta questa caption Instagram originale per un contenuto rigenerato di tipo '{content_type}'.\n\n"
        f"CAPTION ORIGINALE:\n\"\"\"\n{original_caption.strip()}\n\"\"\"\n\n"
        f"TRASCRIZIONE DI SUPPORTO, se utile:\n\"\"\"\n{transcript.strip()}\n\"\"\"\n\n"
        "Regole: mantieni tono e intenzione della caption originale; rimuovi o ripulisci solo elementi non "
        "riutilizzabili (tag persona specifici, call-to-action non adatte, link, testo rotto); non inventare "
        "una caption completamente nuova; conserva gli hashtag utili gia' presenti e aggiungine pochi solo se "
        "servono davvero.\n\n"
        f"Rispondi SOLO con un JSON valido in questa forma esatta: {CAPTION_HASHTAG_SCHEMA_HINT}. "
        "Nessun altro testo, nessun markdown."
    )
    raw = _strip_markdown_fence(run_headless(prompt, allowed_tools=[]).strip())
    if _looks_like_refusal(raw):
        raise ClaudeContentRefusedError(f"Claude ha rifiutato di adattare la caption: {raw[:300]!r}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeCreativeError(f"Caption originale adattata non nel formato JSON atteso: {raw[:300]!r}") from exc

    if "caption" not in data or "hashtags" not in data:
        raise ClaudeCreativeError(f"JSON caption originale incompleto: {data}")
    return {"caption": str(data["caption"]), "hashtags": [str(h) for h in data["hashtags"]]}


def _scene_target_range(character) -> tuple:
    """Range di lunghezza per la SOLA parte scritta da Claude (outfit/posa/
    background). La descrizione fisica fissa + i modificatori obbligatori
    vengono concatenati in codice dopo, non scritti da Claude: si calcola
    quanto spazio resta per la parte di Claude cosi' che il prompt FINALE
    (fisso + scena + fisso) rientri nel target complessivo."""
    fixed_len = len(character.physical_description) + len(character.mandatory_additions) + len(character.negative_prompt)
    separators_len = 6  # ". " tra i 4 segmenti concatenati in _assemble_full_prompt
    return (
        max(0, TARGET_PROMPT_LEN_MIN - fixed_len - separators_len),
        max(0, TARGET_PROMPT_LEN_MAX - fixed_len - separators_len),
    )


def _assemble_full_prompt(character, scene_description: str) -> str:
    return (
        f"{character.physical_description}. {scene_description.strip()}. "
        f"{character.mandatory_additions}. {character.negative_prompt}."
    )


def write_carousel_prompts(
    *, photo_paths: list, character, content_type: str, source_category: str = "", avoid_refusal: bool = False
) -> list:
    """Un prompt di ricostruzione fotorealistica per ciascuna foto in
    `photo_paths` (fino a 3, gia' selezionate da carousel_selection.py).

    Una sola chiamata a Claude con tutte le foto insieme (deciso con
    l'utente), cosi' puo' guardarle e mantenere outfit/background coerenti
    tra loro variando la posa dove serve — le pose cambiano quasi sempre
    da una foto all'altra dello stesso carosello, non ha senso forzarle
    identiche.

    Claude scrive SOLO la parte scena (outfit/posa/background, guardando
    davvero le foto tramite lo strumento Read); la descrizione fisica del
    personaggio e i modificatori obbligatori sono iniettati in codice,
    MAI lasciati alla memoria/parafrasi di Claude — vedi character.py e
    docs/ai-craft-architecture.md §12.
    """
    if not photo_paths:
        raise ValueError("serve almeno una foto")

    scene_min, scene_max = _scene_target_range(character)
    scenes = _generate_scene_descriptions(
        photo_paths=[str(p) for p in photo_paths],
        content_type=content_type,
        source_category=source_category,
        target_min=scene_min,
        target_max=scene_max,
        avoid_refusal=avoid_refusal,
    )
    return [_assemble_full_prompt(character, scene) for scene in scenes]


def _generate_scene_descriptions(
    *, photo_paths: list, content_type: str, source_category: str, target_min: int, target_max: int,
    avoid_refusal: bool = False,
) -> list:
    paths_list = "\n".join(f"- {p}" for p in photo_paths)
    avoid_clause = _REFUSAL_RETRY_CLAUSE if avoid_refusal else ""
    feedback = ""

    for attempt in range(_MAX_SCENE_RETRIES + 1):
        mid = (target_min + target_max) // 2
        budget_outfit = round(mid * 0.35)
        budget_pose = round(mid * 0.30)
        budget_expr = round(mid * 0.20)
        budget_bg = mid - budget_outfit - budget_pose - budget_expr

        prompt = (
            f"Carefully look at these {len(photo_paths)} photos, one at a time (use the file-reading "
            f"tool to open them, they are images):\n{paths_list}\n\n"
            f"They are part of the same Instagram carousel (category '{source_category}', type "
            f"'{content_type}'). For EACH photo, in the given order, write a photorealistic "
            "description to RECREATE THAT EXACT PHOTO — not a generic interpretation of the style, a "
            "faithful reproduction of that specific photo. Write densely and directly: concrete, "
            "identifying facts (exact colors, precise pose/expression points), not atmospheric prose "
            "or redundant adjectives — every word must help recreate the photo, not describe its "
            "mood. In this order, with this rough character budget per section "
            f"(total ~{mid}):\n"
            f"1. OUTFIT (~{budget_outfit} characters): EXACT color of each garment (use the most "
            "precise name possible, e.g. 'dusty pink' not 'pink'), fabric, cut, essential details "
            "(buttons, prints, accessories, jewelry) — list the facts, don't describe their effect "
            "or feel.\n"
            f"2. POSE (~{budget_pose} characters): EXACT body position in that photo — head/torso/"
            "hip angle, where the arms and hands are (what they touch, open or closed), leg "
            "position, gaze direction. Facts, not mood.\n"
            f"3. EXPRESSION (~{budget_expr} characters): eyes open or closed and their direction, "
            "type of smile (mouth closed/open, teeth visible or not), eyebrows, head tilt.\n"
            f"4. BACKGROUND (~{budget_bg} characters): only the essential, identifying visual "
            "elements (setting, 2-3 key objects, type of light) — not an exhaustive list of "
            "everything visible.\n\n"
            "The photos are from the same photoshoot: if outfit and background are the same or "
            "similar across photos, describe them consistently (same colors, same setting); POSE "
            "and EXPRESSION instead almost always change from one photo to another, describe each "
            "photo's specific ones without copying from the others.\n\n"
            f"The TOTAL length of each description must be between {target_min} and {target_max} "
            "characters: the per-section budgets above are a guide to stay in this range, not a "
            "strict rule per point — what matters is that the total is right. If you risk going "
            "over, cut redundant adjectives before cutting concrete facts (colors/positions). Do "
            "NOT use commands or tools to count characters while writing (e.g. bash/wc): they are "
            "not available in this mode and trying will block the response.\n\n"
            "Do NOT include physical descriptions of the person (face, body, hair, skin color): "
            "those are added separately, you only describe outfit/pose/expression/background.\n\n"
            "IMPORTANT for formatting: if you need to quote text/a logo/writing visible in the "
            "photo, NEVER use double quotes \" — use single quotes 'like this' or describe the "
            "text in words instead, otherwise you'll break the JSON of the response.\n\n"
            f"{avoid_clause}{feedback}"
            "Reply ONLY with valid JSON in the exact form "
            '{"scenes": ["<photo 1 description>", "<photo 2 description>", ...]}, '
            "one element per photo, in the same order given above. No other text, no markdown."
        )
        raw = _strip_markdown_fence(run_headless(prompt, allowed_tools=["Read"]).strip())

        if _looks_like_refusal(raw):
            # Rifiuto di policy: non e' un problema di formato, un retry
            # sullo stesso input darebbe lo stesso rifiuto. Interrompe subito
            # invece di consumare i retry pensati per errori di formato.
            raise ClaudeContentRefusedError(f"Claude ha rifiutato di generare il prompt del carosello: {raw[:300]!r}")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            if attempt == _MAX_SCENE_RETRIES:
                raise ClaudeCreativeError(f"Scene carosello non in formato JSON atteso: {raw[:300]!r}")
            feedback = (
                "The previous attempt was not valid JSON (you may have tried to use a tool that "
                "isn't available, e.g. to count characters — don't, just estimate the length "
                "yourself). Reply ONLY with the requested JSON, no other text.\n\n"
            )
            continue

        scenes = data.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != len(photo_paths):
            if attempt == _MAX_SCENE_RETRIES:
                raise ClaudeCreativeError(f"JSON scene carosello incompleto o con numero sbagliato di elementi: {data}")
            n = len(scenes) if isinstance(scenes, list) else "formato errato"
            feedback = f"The previous attempt had {n} elements, exactly {len(photo_paths)} are needed, one per photo.\n\n"
            continue

        lengths = [len(str(s)) for s in scenes]
        out_of_range = [i for i, n in enumerate(lengths) if not (target_min <= n <= target_max)]
        if not out_of_range:
            return [str(s) for s in scenes]

        if attempt == _MAX_SCENE_RETRIES:
            logger.warning(
                "Scene carosello fuori target lunghezza dopo %d tentativi (lunghezze: %s, target: %d-%d caratteri), uso comunque l'ultimo risultato",
                _MAX_SCENE_RETRIES + 1, lengths, target_min, target_max,
            )
            return [str(s) for s in scenes]

        detail = ", ".join(f"photo {i + 1}: {lengths[i]} characters" for i in out_of_range)
        feedback = (
            f"The previous attempt had descriptions outside the target ({detail}; target: "
            f"{target_min}-{target_max} characters each). Rewrite ALL the descriptions respecting "
            "the length target.\n\n"
        )

    raise ClaudeCreativeError("Impossibile generare le scene del carosello")  # unreachable
