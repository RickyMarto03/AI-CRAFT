from aicraft.db.models import CharacterVersion
from aicraft.production import character as character_module
from aicraft.production.character import RUBY2, get_character_for_creator


def test_ruby2_e_registrata_per_creator_ruby():
    assert get_character_for_creator("Ruby") is RUBY2


def test_creator_sconosciuta_ritorna_none():
    assert get_character_for_creator("Nova") is None


def test_ruby2_ha_soul_id_e_modificatori_fissi():
    assert RUBY2.soul_id == "0698f81f-1d26-47bb-b31b-9391aeadb144"
    assert "very big natural breast" in RUBY2.mandatory_additions
    assert "no watermark" in RUBY2.negative_prompt


def test_ruby2_descrizione_fisica_fissata_dalle_foto_di_riferimento():
    # fissata analizzando le foto in data/character_refs/ruby2/, mai
    # improvvisata al volo da Claude in un prompt
    assert RUBY2.physical_description is not None
    assert "hourglass" in RUBY2.physical_description.lower()
    assert "dark brown" in RUBY2.physical_description.lower()


def test_record_versions_if_changed_scrive_al_primo_giro_poi_non_piu(db_session):
    written_1 = character_module.record_versions_if_changed(db_session)
    assert written_1 == len(character_module.CHARACTERS_BY_CREATOR)
    assert db_session.query(CharacterVersion).count() == written_1

    written_2 = character_module.record_versions_if_changed(db_session)
    assert written_2 == 0
    assert db_session.query(CharacterVersion).count() == written_1  # nessun duplicato


def test_record_versions_if_changed_rileva_una_modifica(db_session, monkeypatch):
    character_module.record_versions_if_changed(db_session)

    modificato = character_module.CharacterProfile(
        creator_nome="Ruby",
        soul_id=RUBY2.soul_id,
        soul_name=RUBY2.soul_name,
        physical_description="descrizione cambiata",
        mandatory_additions=RUBY2.mandatory_additions,
        negative_prompt=RUBY2.negative_prompt,
    )
    monkeypatch.setitem(character_module.CHARACTERS_BY_CREATOR, "Ruby", modificato)

    written = character_module.record_versions_if_changed(db_session)

    assert written == 1
    versions = db_session.query(CharacterVersion).order_by(CharacterVersion.id).all()
    assert versions[-1].physical_description == "descrizione cambiata"
    assert len(versions) == 2
