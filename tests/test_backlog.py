import pytest

from aicraft import backlog


def test_add_note_nasce_aperta(db_session):
    note = backlog.add_note(db_session, category="qualita", title="Migliorare fedelta posa")
    assert note.status == "aperto"
    assert note.title == "Migliorare fedelta posa"


def test_list_notes_default_solo_aperte(db_session):
    backlog.add_note(db_session, category="qualita", title="A")
    n2 = backlog.add_note(db_session, category="qualita", title="B")
    backlog.set_status(db_session, n2.id, "fatto")

    aperte = backlog.list_notes(db_session)
    assert [n.title for n in aperte] == ["A"]


def test_list_notes_status_none_ritorna_tutte(db_session):
    n1 = backlog.add_note(db_session, category="qualita", title="A")
    backlog.set_status(db_session, n1.id, "fatto")
    backlog.add_note(db_session, category="qualita", title="B")

    tutte = backlog.list_notes(db_session, status=None)
    assert len(tutte) == 2


def test_list_notes_piu_recenti_prima(db_session):
    backlog.add_note(db_session, category="qualita", title="prima")
    backlog.add_note(db_session, category="qualita", title="seconda")

    notes = backlog.list_notes(db_session, status=None)
    assert notes[0].title == "seconda"


def test_set_status_non_valido_rifiutato(db_session):
    note = backlog.add_note(db_session, category="qualita", title="A")
    with pytest.raises(ValueError):
        backlog.set_status(db_session, note.id, "chissa")


def test_set_status_id_inesistente_rifiutato(db_session):
    with pytest.raises(ValueError):
        backlog.set_status(db_session, 999, "fatto")
