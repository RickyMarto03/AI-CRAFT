"""Impostazioni operatore per feature sperimentali del Production Engine,
attivabili/disattivabili senza toccare codice. Stesso pattern key/value di
AppState gia' usato per il profilo attivo (vedi profiles/manager.py).

SEEDANCE_USE_VIDEO_REFERENCE: se attivo, i video talking/caption passano
anche il video originale come `video_references` a seedance_2_0 (SOLO per
movimento/inquadratura/ritmo camera — l'identita'/outfit/aspetto restano
sempre vincolati alla foto Ruby2 via `start_image` + descrizione fisica
iniettata nel prompt, mai influenzati dal video di riferimento). Default
OFF: deciso con l'utente (15/07/2026) perche' non ancora verificato con una
generazione reale (costo/comportamento sconosciuti) — va acceso a mano
quando si e' pronti a testare. Vedi docs/ai-craft-architecture.md §12.15.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.models import AppState

SEEDANCE_USE_VIDEO_REFERENCE = "seedance_use_video_reference"


def get_flag(session: Session, key: str, *, default: bool = False) -> bool:
    state = session.get(AppState, key)
    if state is None or state.value is None:
        return default
    return state.value == "1"


def set_flag(session: Session, key: str, value: bool) -> None:
    state = session.get(AppState, key)
    if state is None:
        state = AppState(key=key, value="1" if value else "0")
        session.add(state)
    else:
        state.value = "1" if value else "0"
    session.flush()
