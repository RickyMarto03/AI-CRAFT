"""Definizioni fisse dei personaggi Soul usati nella generazione.

Regola dell'utente: un Soul per CREATOR, condiviso da tutti i Profile di
quella creator (non uno per profilo). Per ora esiste una sola creator
("Ruby") con un solo Soul ("Ruby2"): tenuto come costante di codice invece
che come colonna nel DB, dato che non abbiamo ancora un sistema di
migrazioni (nessun Alembic) e la creator reale nel DB e' una sola — se in
futuro arriva una seconda creator/soul, questo va promosso a colonna vera
su Creator con una migrazione. Segnalato qui perche' e' una scelta di
scope, non un dimenticato.

`physical_description` NON e' improvvisata da Claude ad ogni prompt: e'
stata fissata una volta (15/07/2026) analizzando 4 delle foto di
riferimento in data/character_refs/ruby2/ (coerenti tra loro su viso,
capelli, corporatura), poi va riusata sempre uguale in ogni prompt di
generazione — mai rigenerata al volo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CharacterProfile:
    creator_nome: str
    soul_id: str
    soul_name: str
    # None finche' non viene fissata analizzando le foto di riferimento
    # (character_bootstrap.py) — mai generata al volo dentro un prompt.
    physical_description: Optional[str]
    # Aggiunte OBBLIGATORIE ad ogni prompt di generazione per questo
    # personaggio, testo esatto fornito dall'utente, non modificabile da
    # Claude in fase di scrittura del prompt.
    mandatory_additions: str
    negative_prompt: str


RUBY2 = CharacterProfile(
    creator_nome="Ruby",
    soul_id="0698f81f-1d26-47bb-b31b-9391aeadb144",
    soul_name="Ruby2",
    physical_description=(
        "Latina woman in her early-to-mid 20s with a sun-kissed warm olive tan complexion. "
        "Long, straight-to-softly-wavy dark brown hair with warm caramel balayage highlights, "
        "reaching mid-back length. Oval face with a defined jawline and high cheekbones, dark "
        "brown almond-shaped eyes, natural full dark eyebrows with a soft arch, straight refined "
        "nose, full natural pink-nude lips. Hourglass body shape: very curvy figure with wide "
        "full hips and a large, rounded, natural-looking butt, toned smooth tan skin with no "
        "visible tattoos or blemishes."
    ),
    mandatory_additions="very big natural breast, slim waist",
    negative_prompt="no tattoos, no overlay text, no watermark",
)

CHARACTERS_BY_CREATOR = {
    "Ruby": RUBY2,
}


def get_character_for_creator(creator_nome: str) -> Optional[CharacterProfile]:
    return CHARACTERS_BY_CREATOR.get(creator_nome)


def record_versions_if_changed(session: Session) -> int:
    """Snapshotta ogni CharacterProfile in CharacterVersion se e' diverso
    dall'ultimo snapshot registrato per quella creator (o se non ce n'e'
    ancora uno) — costruisce uno storico di "com'era prima" pur restando il
    character una costante di codice, non editabile da UI. Chiamata da
    init_db a ogni avvio: costo trascurabile (una query per creator), scrive
    solo quando davvero cambia qualcosa. Ritorna quanti snapshot nuovi sono
    stati scritti."""
    from ..db.models import CharacterVersion  # import locale: evita un ciclo con db.models

    written = 0
    for char in CHARACTERS_BY_CREATOR.values():
        last = session.scalars(
            select(CharacterVersion)
            .where(CharacterVersion.creator_nome == char.creator_nome)
            .order_by(CharacterVersion.id.desc())
            .limit(1)
        ).first()
        changed = (
            last is None
            or last.physical_description != char.physical_description
            or last.mandatory_additions != char.mandatory_additions
            or last.negative_prompt != char.negative_prompt
        )
        if changed:
            session.add(CharacterVersion(
                creator_nome=char.creator_nome,
                physical_description=char.physical_description,
                mandatory_additions=char.mandatory_additions,
                negative_prompt=char.negative_prompt,
            ))
            written += 1
    if written:
        session.commit()
    return written
