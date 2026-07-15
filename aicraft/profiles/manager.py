"""Step 5 — Multi-profilo. Lo schema supporta il multi-profilo dal punto 1
(Creator 1-N Profile); qui si aggiunge solo la logica di gestione e di
selezione del "profilo attivo" su cui operano i comandi che non lo indicano
esplicitamente.

Distinzione importante:
- `Profile.attivo` (bool): il profilo e' ABILITATO (esiste, si puo' usare).
- "profilo attivo selezionato": quale dei profili e' quello corrente per
  comodita' operativa; memorizzato in AppState (key/value), uno solo alla
  volta. Sono cose diverse.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AppState, Creator, Profile

ACTIVE_PROFILE_KEY = "active_profile_id"
TIPI_CONTENUTO_VALIDI = ("solo_talking", "solo_balletti", "misto")


def create_creator(session: Session, nome: str) -> Creator:
    creator = Creator(nome=nome)
    session.add(creator)
    session.flush()
    return creator


def create_profile(session: Session, *, creator_id: int, nome: str, tipo_contenuto: str, attivo: bool = True) -> Profile:
    if tipo_contenuto not in TIPI_CONTENUTO_VALIDI:
        raise ValueError(f"tipo_contenuto non valido: {tipo_contenuto!r} (attesi {TIPI_CONTENUTO_VALIDI})")
    if session.get(Creator, creator_id) is None:
        raise ValueError(f"Creator {creator_id} inesistente")
    profile = Profile(creator_id=creator_id, nome=nome, tipo_contenuto=tipo_contenuto, attivo=attivo)
    session.add(profile)
    session.flush()
    return profile


def list_creators(session: Session) -> list:
    return list(session.scalars(select(Creator).order_by(Creator.id)))


def list_profiles(session: Session, *, only_attivi: bool = False) -> list:
    stmt = select(Profile).order_by(Profile.id)
    if only_attivi:
        stmt = stmt.where(Profile.attivo.is_(True))
    return list(session.scalars(stmt))


def set_enabled(session: Session, profile_id: int, enabled: bool) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise ValueError(f"Profile {profile_id} inesistente")
    profile.attivo = enabled
    session.flush()
    return profile


def set_active_profile(session: Session, profile_id: int) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise ValueError(f"Profile {profile_id} inesistente")
    state = session.get(AppState, ACTIVE_PROFILE_KEY)
    if state is None:
        state = AppState(key=ACTIVE_PROFILE_KEY, value=str(profile_id))
        session.add(state)
    else:
        state.value = str(profile_id)
    session.flush()
    return profile


def get_active_profile(session: Session) -> Optional[Profile]:
    state = session.get(AppState, ACTIVE_PROFILE_KEY)
    if state is None or state.value is None:
        return None
    return session.get(Profile, int(state.value))


def delete_profile(session: Session, profile_id: int, *, force: bool = False) -> None:
    """Elimina un profilo. Per sicurezza rifiuta se ha piani o contenuti
    collegati (per non orfanare dati), a meno di force=True. Se era il
    profilo attivo selezionato, azzera la selezione."""
    from ..db.models import ContentPiece, PlanWeek

    profile = session.get(Profile, profile_id)
    if profile is None:
        raise ValueError(f"Profile {profile_id} inesistente")

    plans = session.query(PlanWeek).filter(PlanWeek.profile_id == profile_id).all()
    pieces = session.query(ContentPiece).filter(ContentPiece.profile_id == profile_id).all()

    if (plans or pieces) and not force:
        raise ValueError(
            f"Profilo {profile_id} ha {len(plans)} piani e {len(pieces)} contenuti collegati: "
            "rimuovili prima, oppure usa force=True."
        )

    # con force: cancella prima i contenuti (le voci di CreditLedger collegate
    # si scollegano — content_piece_id nullable — mantenendo la storia), poi i
    # piani, infine il profilo, per non lasciare righe orfane.
    for piece in pieces:
        session.delete(piece)
    for plan in plans:
        session.delete(plan)

    state = session.get(AppState, ACTIVE_PROFILE_KEY)
    if state is not None and state.value == str(profile_id):
        state.value = None

    session.delete(profile)
    session.flush()
