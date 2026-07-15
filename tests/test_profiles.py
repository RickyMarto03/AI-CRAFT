"""Test dello Step 5 (multi-profilo) e della selezione profilo attivo."""

import pytest

from aicraft.profiles import manager as profiles


def test_creazione_creator_e_profilo(db_session):
    creator = profiles.create_creator(db_session, "Trinity")
    p = profiles.create_profile(db_session, creator_id=creator.id, nome="Ruby Wilde", tipo_contenuto="misto")
    db_session.commit()
    assert p.id is not None
    assert p.creator_id == creator.id
    assert p.attivo is True


def test_tipo_contenuto_non_valido_rifiutato(db_session):
    creator = profiles.create_creator(db_session, "Trinity")
    with pytest.raises(ValueError):
        profiles.create_profile(db_session, creator_id=creator.id, nome="X", tipo_contenuto="inventato")


def test_profilo_su_creator_inesistente_rifiutato(db_session):
    with pytest.raises(ValueError):
        profiles.create_profile(db_session, creator_id=999, nome="X", tipo_contenuto="misto")


def test_list_profiles_only_attivi(db_session):
    creator = profiles.create_creator(db_session, "Trinity")
    p1 = profiles.create_profile(db_session, creator_id=creator.id, nome="A", tipo_contenuto="misto")
    p2 = profiles.create_profile(db_session, creator_id=creator.id, nome="B", tipo_contenuto="solo_talking")
    profiles.set_enabled(db_session, p2.id, False)
    db_session.commit()

    tutti = profiles.list_profiles(db_session)
    solo_attivi = profiles.list_profiles(db_session, only_attivi=True)
    assert {p.id for p in tutti} == {p1.id, p2.id}
    assert {p.id for p in solo_attivi} == {p1.id}


def test_profilo_attivo_selezionato(db_session):
    creator = profiles.create_creator(db_session, "Trinity")
    p1 = profiles.create_profile(db_session, creator_id=creator.id, nome="A", tipo_contenuto="misto")
    p2 = profiles.create_profile(db_session, creator_id=creator.id, nome="B", tipo_contenuto="misto")
    db_session.commit()

    assert profiles.get_active_profile(db_session) is None

    profiles.set_active_profile(db_session, p1.id)
    db_session.commit()
    assert profiles.get_active_profile(db_session).id == p1.id

    # cambiare selezione sovrascrive, non accumula
    profiles.set_active_profile(db_session, p2.id)
    db_session.commit()
    assert profiles.get_active_profile(db_session).id == p2.id


def test_delete_profile_senza_dipendenze(db_session):
    creator = profiles.create_creator(db_session, "Trinity")
    p1 = profiles.create_profile(db_session, creator_id=creator.id, nome="A", tipo_contenuto="misto")
    p2 = profiles.create_profile(db_session, creator_id=creator.id, nome="B", tipo_contenuto="misto")
    profiles.set_active_profile(db_session, p1.id)
    db_session.commit()

    profiles.delete_profile(db_session, p1.id)
    db_session.commit()

    ids = {p.id for p in profiles.list_profiles(db_session)}
    assert ids == {p2.id}
    # era il profilo attivo: la selezione viene azzerata
    assert profiles.get_active_profile(db_session) is None


def test_delete_profile_con_dipendenze_bloccato(db_session):
    import datetime as dt

    from aicraft.db.models import PlanWeek

    creator = profiles.create_creator(db_session, "Trinity")
    p = profiles.create_profile(db_session, creator_id=creator.id, nome="A", tipo_contenuto="misto")
    db_session.add(PlanWeek(profile=p, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26)))
    db_session.commit()

    with pytest.raises(ValueError):
        profiles.delete_profile(db_session, p.id)
    # con force passa
    profiles.delete_profile(db_session, p.id, force=True)
    db_session.commit()
    assert profiles.list_profiles(db_session) == []


def test_attivo_diverso_da_profilo_selezionato(db_session):
    # Profile.attivo (abilitato) e' indipendente dalla selezione corrente
    creator = profiles.create_creator(db_session, "Trinity")
    p = profiles.create_profile(db_session, creator_id=creator.id, nome="A", tipo_contenuto="misto")
    profiles.set_active_profile(db_session, p.id)
    profiles.set_enabled(db_session, p.id, False)
    db_session.commit()
    # resta il profilo selezionato anche se disabilitato
    active = profiles.get_active_profile(db_session)
    assert active.id == p.id
    assert active.attivo is False
