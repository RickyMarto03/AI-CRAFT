"""Production Engine: fa attraversare a un ContentPiece gli stadi della
pipeline (docs/ai-craft-architecture.md §3), aggiornando lo status ad ogni
passo. Stadi deterministici = codice puro (qa, delivery); stadi creativi =
Claude headless (image_regen/video_regen scrivono il prompt via Claude poi
chiamano Higgsfield con parametri fissi; caption_hashtag e' interamente
creativo). Un fallimento su un pezzo non blocca gli altri, stesso principio
di Reference Sync.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..budget import ledger
from ..db.models import ContentPiece, PlanWeek, Profile, ReferenceItem
from ..reference_sync import allocator
from . import carousel_selection, character, claude_creative, delivery, frame_picker, higgsfield_client, pipeline_spec, qa, settings

logger = logging.getLogger(__name__)

# Dal blueprint §3. "video_caption" compare nell'enum di ContentPiece.content_type
# ma non ha una riga propria in §3: trattato come video_talking finche' non
# viene chiarita una pipeline dedicata — vedi docs/ai-craft-architecture.md §7.
PIPELINE_STAGES = {
    "video_talking": ["image_regen", "video_regen", "qa", "caption_hashtag", "delivery"],
    "video_balletti": ["image_regen", "video_regen", "qa", "caption_hashtag", "delivery"],
    "video_caption": ["image_regen", "video_regen", "qa", "caption_hashtag", "delivery"],
    "carosello": ["image_regen", "qa", "caption_hashtag", "delivery"],
    "stories": ["image_regen", "qa", "delivery"],
}

# Deciso con l'utente (15/07/2026): un video originale piu' lungo di questa
# soglia non viene ricreato — troppo costoso/lungo da analizzare e
# ricostruire fedelmente. Check fatto SUBITO, prima di spendere qualunque
# chiamata Claude/Higgsfield su quel pezzo.
MAX_VIDEO_DURATION_SECONDS = 15.0

# Quanti frame campionare lungo l'intero video originale per l'analisi
# visiva (movimenti/outfit/background) dei talking/caption — vedi
# claude_creative.write_talking_video_prompt e frame_picker.sample_frames.
#
# Un valore fisso basso (5, la versione precedente) copre un video di 15s
# con un frame ogni ~3s: troppo rado per catturare gesti/espressioni che
# cambiano rapidamente — segnalato dall'utente il 15/07/2026 dopo aver
# visto quanto approssimativa restava l'analisi. Ora e' dinamico: circa 1
# frame al secondo (ANALYSIS_FRAMES_PER_SECOND), con un minimo cosi' i clip
# cortissimi restano comunque ben coperti. Costa solo piu' chiamate Read di
# Claude (incluse nell'abbonamento), non crediti Higgsfield.
ANALYSIS_FRAMES_PER_SECOND = 1.0
ANALYSIS_MIN_FRAME_COUNT = 5


def _analysis_frame_count(duration_seconds: float) -> int:
    return max(ANALYSIS_MIN_FRAME_COUNT, round(duration_seconds * ANALYSIS_FRAMES_PER_SECOND))


class VideoTooLongError(RuntimeError):
    """Video originale oltre MAX_VIDEO_DURATION_SECONDS: non recuperabile
    con un retry, il pezzo va scartato con un esito dedicato invece di
    "error" generico (stesso principio di HiggsfieldNSFWBlockedError)."""

    def __init__(self, duration_seconds: float):
        self.duration_seconds = duration_seconds
        super().__init__(
            f"Video di {duration_seconds:.1f}s supera il limite di {MAX_VIDEO_DURATION_SECONDS:.0f}s"
        )


def _log_credit(session: Session, piece: ContentPiece, credits: float, motivo: str) -> None:
    # Unico punto di scrittura sul ledger: budget.ledger, mai CreditLedger a mano
    # (regola ferma CLAUDE.md sui crediti).
    ledger.record_consumption(session, credits=credits, motivo=motivo, content_piece_id=piece.id)
    piece.cost_credits_actual = (piece.cost_credits_actual or 0.0) + abs(credits)


def _select_source_photos(piece: ContentPiece, reference: Optional[ReferenceItem]) -> list:
    """Foto da ricreare per lo stadio image_regen. Caroselli/stories:
    selezione tra le foto gia' scaricate (carousel_selection.py, fino a 3).
    Video (talking/balletti/caption): un frame estratto dal video originale
    (frame_picker.py) — stessa identica procedura di prompt-writing, solo
    una foto sola invece di N. Vedi docs/ai-craft-architecture.md §12.1.
    """
    if reference is None:
        raise RuntimeError("image_regen richiede una reference con materiale scaricato")

    if piece.content_type in ("carosello", "stories"):
        if not reference.frame_paths:
            raise RuntimeError(f"Nessuna foto scaricata per la reference {reference.id} (frame_paths vuoto)")
        return carousel_selection.select_carousel_photos(reference.frame_paths, reference.source_url)

    if not reference.local_video_path:
        raise RuntimeError(f"Nessun video scaricato per la reference {reference.id} (local_video_path vuoto)")
    video_path = Path(reference.local_video_path)

    # Check di idoneita' PRIMA di spendere qualunque chiamata Claude/Higgsfield
    # su questo pezzo — deciso con l'utente, vedi VideoTooLongError sopra.
    duration = qa.get_duration_seconds(video_path)
    if duration > MAX_VIDEO_DURATION_SECONDS:
        raise VideoTooLongError(duration)

    frame_output = video_path.with_name(video_path.stem + "_character_frame.jpg")
    pick = frame_picker.pick_reference_frame(video_path, frame_output)
    return [str(pick.frame_path)]


def _stage_image_regen(session: Session, piece: ContentPiece, reference: Optional[ReferenceItem], profile: Profile) -> None:
    op = pipeline_spec.image_op(piece.content_type)

    creator_nome = profile.creator.nome if profile and profile.creator else None
    char = character.get_character_for_creator(creator_nome) if creator_nome else None
    if char is None:
        raise RuntimeError(f"Nessun personaggio Soul configurato per la creator '{creator_nome}' (vedi character.py)")

    photo_paths = _select_source_photos(piece, reference)
    prompts = claude_creative.write_carousel_prompts(
        photo_paths=photo_paths,
        character=char,
        content_type=piece.content_type,
        source_category=(reference.source_category if reference else "") or "",
    )

    assets = list(piece.generated_assets or [])
    for prompt in prompts:
        # generate create/get non riportano il costo del job: va richiesto a
        # parte con generate cost, PRIMA di lanciare (verificato 14/07/2026).
        cost = higgsfield_client.estimate_cost(op.job_type, prompt=prompt, **op.params)
        result = higgsfield_client.generate_image(prompt, model=op.job_type, custom_reference_id=char.soul_id, **op.params)
        assets.append(result.result_url)
        piece.generated_assets = assets
        if cost:
            _log_credit(session, piece, cost, "image_regen")


def _stage_video_regen(session: Session, piece: ContentPiece, reference: Optional[ReferenceItem], profile: Profile) -> None:
    op = pipeline_spec.video_op(piece.content_type)
    source_image = (piece.generated_assets or [None])[-1]

    if op.job_type == "kling3_0_motion_control":
        # Convenzione di chiamata diversa dagli altri modelli video: niente
        # prompt (verificato con job reale: "prompt": null), serve il video
        # ORIGINALE (non solo la foto Ruby2) come video_references. Vedi
        # docs/ai-craft-architecture.md §12.2.
        #
        # NON VERIFICATO: passiamo qui lo stesso result_url salvato in
        # generated_assets (URL remoto sulla CDN Higgsfield) come
        # image_reference. La documentazione del CLI menziona esplicitamente
        # "UUID (upload id o job id) o local file path" per i media flag, non
        # un URL esterno generico — non e' stato confermato che un URL CDN
        # remoto venga accettato allo stesso modo. Da verificare al prossimo
        # giro reale; se fallisce, il fix e' passare result.job_id
        # dell'immagine invece dell'URL (serve un canale per propagare il
        # job_id tra stadi, oggi non c'e').
        if reference is None or not reference.local_video_path:
            raise RuntimeError("video_balletti richiede il video originale scaricato (reference.local_video_path)")
        try:
            result = higgsfield_client.generate_motion_control(
                image_reference=source_image, video_reference=reference.local_video_path,
            )
        except higgsfield_client.HiggsfieldNSFWBlockedError:
            # Non consuma crediti (verificato) e non e' recuperabile con un
            # retry sullo stesso input: propaga cosi' com'e', process_content_piece
            # la riconosce e marca un esito dedicato invece di "error" generico.
            raise
        assets = list(piece.generated_assets or [])
        assets.append(result.result_url)
        piece.generated_assets = assets
        if op.manual_cost_estimate:
            _log_credit(session, piece, op.manual_cost_estimate, "video_regen (stima non verificata)")
        return

    # seedance_2_0 (video_talking/video_caption): serve il video originale
    # per la trascrizione/analisi visiva, non solo per il check di durata
    # (gia' fatto in _select_source_photos, quindi qui e' garantito <=
    # MAX_VIDEO_DURATION_SECONDS).
    if reference is None or not reference.local_video_path:
        raise RuntimeError(f"{piece.content_type} richiede il video originale scaricato (reference.local_video_path)")
    video_path = Path(reference.local_video_path)

    creator_nome = profile.creator.nome if profile and profile.creator else None
    char = character.get_character_for_creator(creator_nome) if creator_nome else None
    if char is None:
        raise RuntimeError(f"Nessun personaggio Soul configurato per la creator '{creator_nome}' (vedi character.py)")

    duration_seconds = qa.get_duration_seconds(video_path)

    frame_output_dir = video_path.with_name(video_path.stem + "_analysis_frames")
    frame_count = _analysis_frame_count(duration_seconds)
    analysis_frames = frame_picker.sample_frames(video_path, frame_output_dir, count=frame_count)

    use_video_reference = settings.get_flag(session, settings.SEEDANCE_USE_VIDEO_REFERENCE)

    prompt = claude_creative.write_talking_video_prompt(
        frames=analysis_frames,
        transcript=reference.transcript or "",
        transcript_segments=reference.transcript_segments or None,
        character=char,
        content_type=piece.content_type,
        source_category=reference.source_category or "",
        duration_seconds=duration_seconds,
        use_video_reference=use_video_reference,
    )

    # duration reale del video originale (deciso con l'utente), non il
    # worst-case=15 usato solo per la stima di budget in pipeline_spec.py.
    call_params = dict(op.params)
    call_params["duration"] = max(1, round(duration_seconds))
    video_references = [str(video_path)] if use_video_reference else None

    cost = higgsfield_client.estimate_cost(op.job_type, prompt=prompt, **call_params)
    result = higgsfield_client.generate_video(
        prompt, model=op.job_type, start_image=source_image,
        video_references=video_references, **call_params,
    )
    assets = list(piece.generated_assets or [])
    assets.append(result.result_url)
    piece.generated_assets = assets
    if cost:
        _log_credit(session, piece, cost, "video_regen")


def _stage_qa(piece: ContentPiece) -> None:
    if not piece.generated_assets:
        raise RuntimeError("Nessun asset generato da controllare in QA")
    last_asset = Path(piece.generated_assets[-1])
    if piece.content_type in ("carosello", "stories"):
        result = qa.check_image(last_asset)
    else:
        result = qa.check_video(last_asset)
    if not result.passed:
        raise RuntimeError(f"QA fallito su {last_asset}: {result.errors}")


def _stage_caption_hashtag(piece: ContentPiece, reference: Optional[ReferenceItem]) -> None:
    transcript = reference.transcript if reference else ""
    original_caption = (reference.original_caption if reference else "") or ""
    if original_caption.strip():
        data = claude_creative.adapt_original_caption_and_hashtags(
            original_caption=original_caption,
            transcript=transcript or "",
            content_type=piece.content_type,
        )
    else:
        data = claude_creative.write_caption_and_hashtags(transcript=transcript or "", content_type=piece.content_type)
    piece.caption = data["caption"]
    piece.hashtags = data["hashtags"]


def _stage_delivery(piece: ContentPiece, profile: Profile) -> None:
    folder, delivered_assets = delivery.deliver(piece, profile, piece.generated_assets or [])
    piece.generated_assets = delivered_assets
    logger.info("ContentPiece %s consegnato in %s", piece.id, folder)


_STAGE_FUNCS = {
    "image_regen": lambda session, piece, reference, profile: _stage_image_regen(session, piece, reference, profile),
    "video_regen": lambda session, piece, reference, profile: _stage_video_regen(session, piece, reference, profile),
    "qa": lambda session, piece, reference, profile: _stage_qa(piece),
    "caption_hashtag": lambda session, piece, reference, profile: _stage_caption_hashtag(piece, reference),
    "delivery": lambda session, piece, reference, profile: _stage_delivery(piece, profile),
}


def process_content_piece(session: Session, piece: ContentPiece) -> None:
    stages = PIPELINE_STAGES.get(piece.content_type)
    if stages is None:
        logger.error("content_type sconosciuto per ContentPiece %s: %s", piece.id, piece.content_type)
        piece.status = "error"
        session.commit()
        return

    reference = piece.reference
    profile = piece.profile

    try:
        for stage in stages:
            piece.status = stage
            session.commit()
            _STAGE_FUNCS[stage](session, piece, reference, profile)
            session.commit()
        piece.status = "delivered"
        session.commit()
    except higgsfield_client.HiggsfieldNSFWBlockedError:
        # Esito legittimo e non recuperabile con un retry (stesso input ->
        # stesso blocco), distinto da un errore tecnico generico. Vedi
        # docs/ai-craft-architecture.md §12.2/§12.6.
        logger.warning("ContentPiece %s bloccato da moderazione content (nsfw)", piece.id)
        session.rollback()
        piece.status = "blocked_nsfw"
        session.commit()
    except VideoTooLongError as exc:
        # Idem: esito legittimo (video originale troppo lungo per essere
        # ricreato), non un errore tecnico — deciso con l'utente.
        logger.info("ContentPiece %s scartato: %s", piece.id, exc)
        session.rollback()
        piece.status = "too_long"
        session.commit()
    except Exception as exc:
        logger.exception("Errore nello stadio '%s' per ContentPiece %s", piece.status, piece.id)
        session.rollback()
        piece.status = "error"
        session.commit()


def run_once(session: Session, *, plan_id: int | None = None) -> dict:
    from sqlalchemy import select

    plan_stmt = select(PlanWeek).where(PlanWeek.status == "approvato")
    if plan_id is not None:
        plan_stmt = plan_stmt.where(PlanWeek.id == plan_id)
    approved_plans = session.scalars(plan_stmt).all()
    assigned_total = 0
    missing_total = 0
    for plan in approved_plans:
        result = allocator.assign_references_to_plan(session, plan.id)
        assigned_total += result.assigned
        missing_total += result.missing
        if result.assigned:
            logger.info(
                "Assegnate %d reference al piano %s prima della produzione (%d mancanti)",
                result.assigned,
                plan.id,
                result.missing,
            )
    session.commit()

    # Si producono solo i pezzi di piani APPROVATI (blueprint §2: "per ogni
    # ContentPiece approvato"). Il join esclude naturalmente i pezzi senza
    # piano o in piani ancora in bozza.
    pending_stmt = (
        select(ContentPiece)
        .join(PlanWeek, ContentPiece.plan_week_id == PlanWeek.id)
        .where(
            ContentPiece.status == "reference_ready",
            ContentPiece.reference_id.is_not(None),
            PlanWeek.status == "approvato",
        )
    )
    if plan_id is not None:
        pending_stmt = pending_stmt.where(ContentPiece.plan_week_id == plan_id)
    pending = session.scalars(pending_stmt).all()
    logger.info("%d ContentPiece pronti per la produzione (piani approvati)", len(pending))
    before = {piece.id: piece.status for piece in pending}
    for piece in pending:
        process_content_piece(session, piece)
    session.flush()
    return {
        "approved_plans": len(approved_plans),
        "assigned_references": assigned_total,
        "missing_references": missing_total,
        "processed": len(pending),
        "delivered": sum(1 for piece in pending if piece.status == "delivered"),
        "failed": sum(1 for piece in pending if piece.status not in ("delivered", before.get(piece.id))),
    }
