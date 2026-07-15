from aicraft.production import naming


def test_slugify_normalizza_spazi_e_maiuscole():
    assert naming.slugify("Ruby Wilde") == "ruby-wilde"


def test_slugify_gestisce_caratteri_speciali():
    assert naming.slugify("Video / Talking!!") == "video-talking"


def test_slugify_stringa_vuota_ha_fallback():
    assert naming.slugify("   ") == "senza-nome"


def test_content_piece_folder_e_deterministico():
    folder1 = naming.content_piece_folder(
        profile_nome="Ruby Wilde", content_type="carosello", piece_id=42,
        week_start="2026-07-20", scheduled_day="lun",
    )
    folder2 = naming.content_piece_folder(
        profile_nome="Ruby Wilde", content_type="carosello", piece_id=42,
        week_start="2026-07-20", scheduled_day="lun",
    )
    assert folder1 == folder2
    assert folder1.name == "2026-07-20_lun_42"
    assert "ruby-wilde" in folder1.parts
    assert "carosello" in folder1.parts


def test_asset_filename_padding():
    assert naming.asset_filename(1, ".mp4") == "asset_01.mp4"
    assert naming.asset_filename(12, "jpg") == "asset_12.jpg"
