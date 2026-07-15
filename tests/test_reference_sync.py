import datetime as dt

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
