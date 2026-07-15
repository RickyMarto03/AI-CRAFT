import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft.db.base import Base
from aicraft.db.models import ContentPiece, Creator, PlanWeek, Profile, ReferenceItem
from aicraft.reference_sync import sync
from aicraft.reference_sync.sheets_reader import SheetReference


def test_upsert_reference_salva_settimana_posizione_e_categoria(db_session):
    ref = SheetReference(
        url="https://www.instagram.com/p/AAA/",
        source_tab="CAROSELLI",
        source_category="BOOBS",
        content_type_hint="carosello",
        week_start=dt.date(2026, 7, 13),
        week_end=dt.date(2026, 7, 19),
        sheet_row_id="CAROSELLI!R3C1",
        sheet_order=12,
        sheet_row=3,
        sheet_col=1,
    )

    item = sync.upsert_reference(db_session, ref)
    db_session.commit()

    assert item.week_start == dt.date(2026, 7, 13)
    assert item.source_category == "BOOBS"
    assert item.sheet_order == 12
    assert item.sheet_row == 3
    assert item.sheet_col == 1


def test_media_folder_per_reference_usa_settimana_tab_categoria():
    ref = SheetReference(
        url="https://www.instagram.com/p/ABC123/?img_index=2",
        source_tab="VIRAL GENERAL",
        source_category="BALLETTI/LIPSYNC",
        content_type_hint="video",
        week_start=dt.date(2026, 7, 13),
        week_end=dt.date(2026, 7, 19),
        sheet_row_id="VIRAL GENERAL!R10C6",
        sheet_order=1,
        sheet_row=10,
        sheet_col=6,
    )

    path = sync.media_folder_for_reference(ref)

    assert str(path).endswith("2026-W29/VIRAL_GENERAL/BALLETTI_LIPSYNC/ABC123")


def test_parse_sync_policy_legge_limiti_per_categoria():
    items = sync.parse_sync_policy("CAROSELLI:BOOBS=2,VIRAL GENERAL:TALKING=4")

    assert [(i.source_tab, i.source_category, i.limit) for i in items] == [
        ("CAROSELLI", "BOOBS", 2),
        ("VIRAL GENERAL", "TALKING", 4),
    ]


def test_status_for_processing_error_distingue_casi_utili():
    class MediaNotFound(Exception):
        pass

    class ClientUnauthorizedError(Exception):
        pass

    assert sync._status_for_processing_error(MediaNotFound("missing")) == "unavailable"
    assert sync._status_for_processing_error(ClientUnauthorizedError("login required")) == "private"
    assert sync._status_for_processing_error(RuntimeError("ffmpeg failed"), current_status="transcribing") == "transcription_error"
    assert sync._status_for_processing_error(RuntimeError("timeout")) == "download_error"


def test_retryable_statuses_copre_tutti_gli_stati_di_errore_granulari():
    """Regressione (15/07/2026): run_once() aveva una RETRYABLE_STATUSES piu'
    corta di run_policy_once(), quindi un item fallito con uno stato granulare
    (download_error/unavailable/private/transcription_error) restava bloccato
    per sempre con `references sync` mentre veniva ritentato solo con
    `sync-policy`. Ora entrambe le funzioni leggono la stessa costante: questo
    test blocca il caso in cui uno stato prodotto da _status_for_processing_error
    smetta di essere ritentabile."""
    stati_possibili = {"unavailable", "private", "transcription_error", "download_error"}
    assert stati_possibili <= set(sync.RETRYABLE_STATUSES)


def test_cleanup_old_references_cancella_solo_reference_e_scollega_content(tmp_path, db_session, monkeypatch):
    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setattr(sync.config, "MEDIA_DIR", media_root)
    old_file = media_root / "2026-W20" / "CAROSELLI" / "BOOBS" / "old" / "a.jpg"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("x")

    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="ruby", tipo_contenuto="misto")
    plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26))
    ref = ReferenceItem(
        source_url="old",
        source_tab="CAROSELLI",
        source_category="BOOBS",
        content_type_hint="carosello",
        week_start=dt.date(2026, 5, 11),
        week_end=dt.date(2026, 5, 17),
        status="ready",
        frame_paths=[str(old_file)],
    )
    piece = ContentPiece(profile=profile, plan_week=plan, content_type="carosello", reference=ref)
    db_session.add_all([creator, profile, plan, ref, piece])
    db_session.commit()

    deleted = sync.cleanup_old_references(db_session, today=dt.date(2026, 7, 15))
    db_session.commit()

    assert deleted == 1
    assert db_session.get(ReferenceItem, ref.id) is None
    assert piece.reference_id is None
    assert not old_file.exists()


def _isolated_session_factory(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_retry_reference_carica_item_e_chiama_process_item(monkeypatch, tmp_path):
    """retry_reference apre una PROPRIA sessione (azione manuale isolata
    dalla Libreria, non serve un nuovo giro di lettura sheet): qui la
    isoliamo su un DB temporaneo dedicato invece di riusare db_session."""
    TestSession = _isolated_session_factory(tmp_path, "retry.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    with TestSession() as session:
        item = ReferenceItem(source_url="https://www.instagram.com/p/RETRY/", status="download_error")
        session.add(item)
        session.commit()
        item_id = item.id

    seen = {}

    def fake_process_item(session, item, **kw):
        seen["called_with_id"] = item.id
        seen["sheet_ref"] = kw.get("sheet_ref")
        item.status = "ready"

    monkeypatch.setattr(sync, "process_item", fake_process_item)

    result = sync.retry_reference(item_id)

    assert seen["called_with_id"] == item_id
    assert result == {"id": item_id, "status": "ready", "error_message": None}


def test_retry_reference_id_inesistente_solleva_errore(monkeypatch, tmp_path):
    TestSession = _isolated_session_factory(tmp_path, "retry2.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    with pytest.raises(ValueError):
        sync.retry_reference(999)
