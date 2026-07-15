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
