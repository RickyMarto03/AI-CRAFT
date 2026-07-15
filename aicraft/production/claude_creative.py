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
            f"Segmenti della trascrizione CON TIMESTAMP ESATTI (usa questi secondi per capire quale frame "
            f"corrisponde a quale frase — non indovinare l'allineamento, i numeri sopra i frame e qui sotto sono "
            f"nella stessa scala temporale del video originale):\n{segments_list}\n\n"
            f"Trascrizione completa in un unico blocco, come riferimento:\n\"\"\"\n{transcript.strip()}\n\"\"\""
        )
    else:
        transcript_block = (
            f"Questa e' la trascrizione ESATTA di cio' che dice la persona nel video (senza timestamp per "
            f"segmento disponibili: deduci l'allineamento con i frame dall'ordine e dal contenuto):\n\"\"\"\n"
            f"{transcript.strip()}\n\"\"\""
        )

    if use_video_reference:
        reference_clause = (
            "Il video originale verra' passato al modello SOLO come riferimento di movimento/inquadratura/ritmo "
            "della camera — NON deve influenzare aspetto fisico, outfit, colori o identita': quelli vengono SOLO "
            "dalla foto di riferimento fornita a parte. Scrivi la sezione REFERENCE USAGE dichiarando "
            "esplicitamente questo: identita'/outfit/aspetto dalla foto di riferimento, movimento/framing dal "
            "video di riferimento, senza ridisegnare personaggio o ambientazione."
        )
    else:
        reference_clause = (
            "Non c'e' nessun video di riferimento passato al modello, solo la foto. Scrivi la sezione REFERENCE "
            "USAGE dichiarando che identita'/outfit/ambientazione vengono dalla foto di riferimento, e descrivi TU "
            "a parole, con precisione, ogni movimento di camera/corpo/mani/testa osservato nei frame: e' l'unico "
            "modo in cui il modello sapra' come muoversi."
        )

    prompt = (
        f"Guarda con attenzione questi {len(frames)} frame campionati lungo l'intero video originale, ciascuno "
        f"etichettato col secondo esatto in cui compare nel video (usa lo strumento di lettura file, sono "
        f"immagini):\n{paths_list}\n\n"
        f"E' un video Instagram di tipo '{content_type}' (categoria '{source_category}'), durata originale "
        f"{duration_seconds:.1f} secondi. {transcript_block}\n\n"
        "Scrivi UN prompt cinematografico completo in inglese per rigenerare questo video con un nuovo modello, "
        "seguendo questa struttura (stesso formato di prompt reali gia' usati con successo su questa "
        "piattaforma):\n\n"
        f"REFERENCE USAGE: {reference_clause}\n\n"
        "STYLE: stile visivo generale osservato nei frame (es. selfie smartphone realistico, inquadratura, "
        "ambiente/luce).\n\n"
        "ACTION/PERFORMANCE: descrivi i movimenti di corpo/mani/testa/sguardo ed espressioni facciali osservati "
        "nei frame, collegandoli ALLE FRASI ESATTE del dialogo (cita la frase tra virgolette singole, poi descrivi "
        "il movimento che l'accompagna) — usa i timestamp di frame e segmenti per abbinare con precisione quale "
        "frase viene detta in quale momento/frame, invece di indovinare dall'ordine. Segui l'ordine del dialogo "
        "dall'inizio alla fine.\n\n"
        "CAMERA: posizione/movimento della camera osservato nei frame (es. camera fissa smartphone, leggero "
        "movimento a mano, nessun taglio se i frame mostrano continuita').\n\n"
        f"PACING: ritmo per stare in {duration_seconds:.1f} secondi totali, dichiara esplicitamente la durata nel "
        "testo (es. 'Create a X-second single continuous take...').\n\n"
        "DIALOGUE AND AUDIO: riporta il dialogo tra virgolette singole nell'ordine esatto della trascrizione "
        "fornita sopra — puoi correggere SOLO refusi evidenti di trascrizione automatica (ripetizioni per errore, "
        "'ehm' isolati), NON puoi cambiare il significato, l'ordine, o aggiungere frasi che non ci sono nella "
        "trascrizione. Specifica tono vocale e ritmo di consegna.\n\n"
        "CONSTRAINTS: nessun taglio di montaggio (single continuous shot) salvo che i frame mostrino chiaramente "
        "il contrario, nessuno zoom innaturale, nessun testo/sottotitoli/loghi/watermark in sovrimpressione, "
        "nessuna musica di sottofondo, nessun dialogo diverso da quello fornito, nessun errore di sincronismo "
        "labiale.\n\n"
        "Scrivi in modo denso e concreto (fatti visivi precisi, non prosa atmosferica). NON includere descrizioni "
        "fisiche della persona (viso, corpo, capelli, colore pelle, outfit): quelle vengono aggiunte separatamente "
        "in codice, tu scrivi solo le sezioni sopra.\n\n"
        "IMPORTANTE per il formato: se devi citare una scritta/testo visibile nei frame o nel dialogo, usa le "
        "virgolette singole 'cosi'', mai le virgolette doppie.\n\n"
        "Rispondi SOLO con il testo del prompt finale (le sezioni sopra, con le intestazioni in maiuscolo come "
        "negli esempi reali), nessun commento, nessun markdown."
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


def write_carousel_prompts(*, photo_paths: list, character, content_type: str, source_category: str = "") -> list:
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
    )
    return [_assemble_full_prompt(character, scene) for scene in scenes]


def _generate_scene_descriptions(
    *, photo_paths: list, content_type: str, source_category: str, target_min: int, target_max: int
) -> list:
    paths_list = "\n".join(f"- {p}" for p in photo_paths)
    feedback = ""

    for attempt in range(_MAX_SCENE_RETRIES + 1):
        mid = (target_min + target_max) // 2
        budget_outfit = round(mid * 0.35)
        budget_pose = round(mid * 0.30)
        budget_expr = round(mid * 0.20)
        budget_bg = mid - budget_outfit - budget_pose - budget_expr

        prompt = (
            f"Guarda con attenzione, una per una, queste {len(photo_paths)} foto (usa lo strumento di lettura "
            f"file per aprirle, sono immagini):\n{paths_list}\n\n"
            f"Fanno parte dello stesso carosello Instagram (categoria '{source_category}', tipo '{content_type}'). "
            "Per CIASCUNA foto, nell'ordine dato, scrivi una descrizione fotorealistica per RICREARE ESATTAMENTE "
            "quella foto — non un'interpretazione generica dello stile, la riproduzione fedele di quella foto "
            "specifica. Scrivi in modo denso e diretto: fatti concreti e identificativi (colori esatti, punti "
            "precisi di posa/espressione), non prosa atmosferica o aggettivi ridondanti — ogni parola deve "
            "aiutare a ricreare la foto, non descriverne l'atmosfera. In quest'ordine, con questo budget di "
            f"caratteri indicativo per parte (totale ~{mid}):\n"
            f"1. OUTFIT (~{budget_outfit} caratteri): colore ESATTO di ogni capo (usa il nome piu' preciso "
            "possibile, es. 'rosa cipria' non 'rosa'), tessuto, taglio, dettagli essenziali (bottoni, stampe, "
            "accessori, gioielli) — elenca i fatti, senza descriverne l'effetto o la sensazione.\n"
            f"2. POSA (~{budget_pose} caratteri): posizione ESATTA del corpo in quella foto — angolazione di "
            "testa/busto/bacino, dove sono braccia e mani (cosa toccano, aperte o chiuse), posizione delle "
            "gambe, direzione dello sguardo. Fatti, non atmosfera.\n"
            f"3. ESPRESSIONE (~{budget_expr} caratteri): occhi aperti o chiusi e direzione, tipo di sorriso "
            "(bocca chiusa/aperta, denti visibili o no), sopracciglia, inclinazione testa.\n"
            f"4. BACKGROUND (~{budget_bg} caratteri): solo gli elementi visivi essenziali e identificativi "
            "(ambiente, 2-3 oggetti chiave, tipo di luce) — non un elenco esaustivo di tutto cio' che si vede.\n\n"
            "Le foto sono dello stesso servizio fotografico: se outfit e background sono uguali o simili tra "
            "le foto, descrivili in modo coerente tra loro (stessi colori, stesso ambiente); la POSA e "
            "l'ESPRESSIONE invece cambiano quasi sempre da una foto all'altra, descrivi quelle specifiche di "
            "ogni foto senza copiarle dalle altre.\n\n"
            f"Il TOTALE di ogni descrizione deve stare tra {target_min} e {target_max} caratteri: i budget per "
            "parte sopra sono una guida per restare in questo range, non un obbligo rigido punto per punto — "
            "conta che il totale sia giusto. Se rischi di sforare, taglia aggettivi ridondanti prima di tagliare "
            "fatti concreti (colori/posizioni). NON usare comandi o strumenti per contare i caratteri mentre "
            "scrivi (es. bash/wc): non sono disponibili in questa modalita' e il tentativo blocca la risposta.\n\n"
            "NON includere descrizioni fisiche della persona (viso, corpo, capelli, colore pelle): quelle "
            "vengono aggiunte separatamente, tu descrivi solo outfit/posa/espressione/background.\n\n"
            "IMPORTANTE per il formato: se devi citare una scritta/logo/testo visibile in foto, NON usare mai "
            "le virgolette doppie \" — usa le virgolette singole 'cosi'' oppure descrivi la scritta a parole, "
            "altrimenti rompi il JSON della risposta.\n\n"
            f"{feedback}"
            "Rispondi SOLO con un JSON valido nella forma esatta "
            '{"scenes": ["<descrizione foto 1>", "<descrizione foto 2>", ...]}, '
            "un elemento per foto, nello stesso ordine dato sopra. Nessun altro testo, nessun markdown."
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
                "Il tentativo precedente non era JSON valido (potresti aver provato a usare uno strumento non "
                "disponibile, es. per contare i caratteri — non farlo, stima la lunghezza da solo). "
                "Rispondi SOLO con il JSON richiesto, nessun altro testo.\n\n"
            )
            continue

        scenes = data.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != len(photo_paths):
            if attempt == _MAX_SCENE_RETRIES:
                raise ClaudeCreativeError(f"JSON scene carosello incompleto o con numero sbagliato di elementi: {data}")
            n = len(scenes) if isinstance(scenes, list) else "formato errato"
            feedback = f"Il tentativo precedente aveva {n} elementi, ne servono esattamente {len(photo_paths)}, uno per foto.\n\n"
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

        detail = ", ".join(f"foto {i + 1}: {lengths[i]} caratteri" for i in out_of_range)
        feedback = (
            f"Il tentativo precedente aveva descrizioni fuori target ({detail}; target: {target_min}-{target_max} "
            "caratteri ciascuna). Riscrivi TUTTE le descrizioni rispettando il target di lunghezza.\n\n"
        )

    raise ClaudeCreativeError("Impossibile generare le scene del carosello")  # unreachable
