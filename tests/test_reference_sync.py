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


def test_is_retryable_rispetta_stato_e_tetto_tentativi():
    ok = ReferenceItem(status="download_error", download_attempts=1)
    esaurito = ReferenceItem(status="unavailable", download_attempts=sync.MAX_DOWNLOAD_ATTEMPTS)
    mai_provato = ReferenceItem(status="pending", download_attempts=0)
    non_fallito = ReferenceItem(status="ready", download_attempts=0)

    assert sync._is_retryable(ok) is True
    assert sync._is_retryable(esaurito) is False
    assert sync._is_retryable(mai_provato) is True
    assert sync._is_retryable(non_fallito) is False


def test_process_item_incrementa_tentativi_e_segna_unavailable_dopo_il_tetto(db_session, monkeypatch):
    def fake_download(*args, **kwargs):
        raise RuntimeError("timeout di rete")

    monkeypatch.setattr(sync.downloader, "download_reference", fake_download)

    item = ReferenceItem(source_url="https://www.instagram.com/p/CAP/", status="pending")
    db_session.add(item)
    db_session.commit()

    sync.process_item(db_session, item)
    assert item.download_attempts == 1
    assert item.status == "download_error"  # sotto il tetto: stato granulare normale

    sync.process_item(db_session, item)
    assert item.download_attempts == sync.MAX_DOWNLOAD_ATTEMPTS
    assert item.status == "unavailable"
    assert "2 tentativi" in item.error_message
    assert sync._is_retryable(item) is False


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
    assert result == {"id": item_id, "status": "ready", "error_message": None, "skipped": False}


def test_retry_reference_id_inesistente_solleva_errore(monkeypatch, tmp_path):
    TestSession = _isolated_session_factory(tmp_path, "retry2.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    with pytest.raises(ValueError):
        sync.retry_reference(999)


def test_retry_all_ritenta_in_sequenza_e_conta_esiti(monkeypatch, tmp_path):
    TestSession = _isolated_session_factory(tmp_path, "retry3.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    with TestSession() as session:
        a = ReferenceItem(source_url="https://www.instagram.com/p/A/", status="download_error")
        b = ReferenceItem(source_url="https://www.instagram.com/p/B/", status="download_error")
        session.add_all([a, b])
        session.commit()
        id_a, id_b = a.id, b.id

    seen_ids = []

    def fake_process_item(session, item, **kw):
        seen_ids.append(item.id)
        item.status = "ready" if item.id == id_a else "download_error"

    monkeypatch.setattr(sync, "process_item", fake_process_item)

    result = sync.retry_all([id_a, id_b])

    assert seen_ids == [id_a, id_b]  # ordine rispettato, stesso rate-limit del retry singolo
    assert result == {"total": 2, "ready": 1, "still_failed": 1}


def test_retry_all_lista_vuota_non_esplode(monkeypatch, tmp_path):
    TestSession = _isolated_session_factory(tmp_path, "retry4.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    assert sync.retry_all([]) == {"total": 0, "ready": 0, "still_failed": 0}


def test_retry_stale_errors_ritenta_solo_i_falliti_vecchi(monkeypatch, tmp_path):
    TestSession = _isolated_session_factory(tmp_path, "retry5.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    now = dt.datetime.utcnow()
    with TestSession() as session:
        vecchio = ReferenceItem(source_url="https://www.instagram.com/p/OLD/", status="download_error")
        recente = ReferenceItem(source_url="https://www.instagram.com/p/NEW/", status="download_error")
        non_fallito = ReferenceItem(source_url="https://www.instagram.com/p/READY/", status="ready")
        session.add_all([vecchio, recente, non_fallito])
        session.commit()
        # forza updated_at (onupdate non scatta sull'insert iniziale)
        vecchio.error_message = "x"
        recente.error_message = "x"
        session.commit()
        vecchio_id, recente_id = vecchio.id, recente.id

    # vecchio: fallito 10 giorni fa (fuori dalla finestra di 3gg -> ritentabile)
    # recente: fallito 1 giorno fa (dentro la finestra -> NON ritentabile)
    with TestSession() as session:
        session.query(ReferenceItem).filter_by(id=vecchio_id).update({"updated_at": now - dt.timedelta(days=10)})
        session.query(ReferenceItem).filter_by(id=recente_id).update({"updated_at": now - dt.timedelta(days=1)})
        session.commit()

    seen_ids = []

    def fake_retry_reference(reference_id):
        seen_ids.append(reference_id)
        return {"id": reference_id, "status": "ready", "error_message": None}

    monkeypatch.setattr(sync, "retry_reference", fake_retry_reference)

    result = sync.retry_stale_errors(older_than_days=3)

    assert seen_ids == [vecchio_id]
    assert result["total"] == 1
    assert result["older_than_days"] == 3


def test_retry_reference_non_ritenta_reference_con_tentativi_esauriti(monkeypatch, tmp_path):
    TestSession = _isolated_session_factory(tmp_path, "retry6.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    with TestSession() as session:
        item = ReferenceItem(
            source_url="https://www.instagram.com/p/EXHAUSTED/",
            status="unavailable",
            download_attempts=sync.MAX_DOWNLOAD_ATTEMPTS,
        )
        session.add(item)
        session.commit()
        item_id = item.id

    called = {"n": 0}

    def fake_process_item(session, item, **kw):
        called["n"] += 1

    monkeypatch.setattr(sync, "process_item", fake_process_item)

    result = sync.retry_reference(item_id)

    assert called["n"] == 0  # non deve consumare un altro tentativo reale
    assert result == {"id": item_id, "status": "unavailable", "error_message": None, "skipped": True}


def test_retry_stale_errors_esclude_reference_con_tentativi_esauriti(monkeypatch, tmp_path):
    TestSession = _isolated_session_factory(tmp_path, "retry7.db")
    monkeypatch.setattr(sync, "SessionLocal", TestSession)
    monkeypatch.setattr(sync, "init_db", lambda: None)

    now = dt.datetime.utcnow()
    with TestSession() as session:
        ritentabile = ReferenceItem(source_url="https://www.instagram.com/p/RT/", status="download_error", download_attempts=1)
        esaurita = ReferenceItem(source_url="https://www.instagram.com/p/EX/", status="unavailable", download_attempts=sync.MAX_DOWNLOAD_ATTEMPTS)
        session.add_all([ritentabile, esaurita])
        session.commit()
        ritentabile.error_message = "x"
        esaurita.error_message = "x"
        session.commit()
        id_ritentabile, id_esaurita = ritentabile.id, esaurita.id

    with TestSession() as session:
        session.query(ReferenceItem).filter(ReferenceItem.id.in_([id_ritentabile, id_esaurita])).update(
            {"updated_at": now - dt.timedelta(days=10)}, synchronize_session=False
        )
        session.commit()

    seen_ids = []

    def fake_retry_reference(reference_id):
        seen_ids.append(reference_id)
        return {"id": reference_id, "status": "ready", "error_message": None, "skipped": False}

    monkeypatch.setattr(sync, "retry_reference", fake_retry_reference)

    result = sync.retry_stale_errors(older_than_days=3)

    assert seen_ids == [id_ritentabile]
    assert result["total"] == 1
