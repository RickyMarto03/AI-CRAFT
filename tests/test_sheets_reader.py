"""Test del parser dello sheet, con fixture trascritte a mano dai due tab
osservati (CAROSELLI, VIRAL GENERAL). Non serve gspread/rete: parse_rows
lavora solo su liste di liste di stringhe, come restituite da
worksheet.get_all_values().
"""

import datetime as dt

from aicraft.reference_sync.sheets_reader import parse_rows

YEAR = 2026


def test_caroselli_tab_estrae_link_per_categoria_e_settimana():
    rows = [
        ["18-24 may", "", "", ""],
        ["BOOBS", "BOOTY", "GENERAL", ""],
        [
            "https://www.instagram.com/p/AAA1/",
            "https://www.instagram.com/p/BBB1/",
            "https://www.instagram.com/p/CCC1/",
            "",
        ],
        [
            "https://www.instagram.com/p/AAA2/",
            "https://www.instagram.com/p/BBB2/",
            "https://www.instagram.com/p/CCC2/",
            "",
        ],
        [
            "https://www.instagram.com/p/AAA3/",
            "https://www.instagram.com/p/BBB3/",
            "https://www.instagram.com/p/CCC3/",
            "Farm",  # nota extra in colonna senza header di categoria: va ignorata
        ],
        ["25-31 may", "", "", ""],
        ["BOOBS", "BOOTY", "GENERAL", ""],
        [
            "https://www.instagram.com/p/AAA4/",
            "",
            "https://www.instagram.com/p/CCC4/",
            "",
        ],
    ]

    refs = parse_rows(rows, tab_name="CAROSELLI", year=YEAR)

    # 3 categorie x 3 righe (prima settimana) + 2 link (seconda settimana, BOOTY vuoto)
    assert len(refs) == 9 + 2

    first_week = [r for r in refs if r.week_start == dt.date(2026, 5, 18)]
    assert len(first_week) == 9
    assert all(r.week_end == dt.date(2026, 5, 24) for r in first_week)
    assert all(r.content_type_hint == "carosello" for r in first_week)
    assert {r.source_category for r in first_week} == {"BOOBS", "BOOTY", "GENERAL"}

    second_week = [r for r in refs if r.week_start == dt.date(2026, 5, 25)]
    assert len(second_week) == 2
    assert {r.url for r in second_week} == {
        "https://www.instagram.com/p/AAA4/",
        "https://www.instagram.com/p/CCC4/",
    }

    # nessuna reference spuria dalla nota "Farm" in colonna non mappata
    assert all("Farm" not in r.url for r in refs)


def test_caroselli_tab_sheet_row_id_punta_alla_cella_corretta():
    rows = [
        ["18-24 may", "", "", ""],
        ["BOOBS", "BOOTY", "GENERAL", ""],
        ["https://www.instagram.com/p/AAA1/", "", "", ""],
    ]

    refs = parse_rows(rows, tab_name="CAROSELLI", year=YEAR)

    assert len(refs) == 1
    assert refs[0].sheet_row_id == "CAROSELLI!R3C1"


def test_viral_general_tab_data_annegata_nell_header_categoria():
    rows = [
        # riga 192: header di gruppo categoria
        ["OTHER CONTENTS", "", "", "", "", "BALLETTI/LIPSYNC", "", "", "", "", "TALKING", "", "", "", "", "CAPTION"],
        # riga 193: sub-header DONE <persona> (da ignorare) + data annegata nella colonna TALKING
        ["", "DONE NICO", "DONE RICKY", "DONE ANDRE", "DONE MATTE", "", "DONE NICO", "DONE RICKY", "DONE ANDRE", "DONE MATTE", "20-26th JULY", "DONE NICO", "DONE RICKY", "DONE ANDRE", "DONE MATTE", ""],
        # riga 194: dati
        [
            "https://www.instagram.com/reel/OTH1/",
            "",
            "",
            "",
            "",
            "https://www.instagram.com/reel/BAL1/",
            "",
            "",
            "",
            "",
            "https://www.instagram.com/reel/TALK1/",
            "",
            "",
            "",
            "",
            "",
        ],
    ]

    refs = parse_rows(rows, tab_name="VIRAL GENERAL", year=YEAR)

    assert len(refs) == 3
    by_category = {r.source_category: r for r in refs}

    assert by_category["OTHER CONTENTS"].url == "https://www.instagram.com/reel/OTH1/"
    assert by_category["BALLETTI/LIPSYNC"].url == "https://www.instagram.com/reel/BAL1/"
    assert by_category["TALKING"].url == "https://www.instagram.com/reel/TALK1/"
    assert by_category["OTHER CONTENTS"].done_ricky_col == 3
    assert by_category["BALLETTI/LIPSYNC"].done_ricky_col == 8
    assert by_category["TALKING"].done_ricky_col == 13

    # la data, pur annegata in una cella di header categoria, va applicata a tutte le righe dati del blocco
    for r in refs:
        assert r.week_start == dt.date(2026, 7, 20)
        assert r.week_end == dt.date(2026, 7, 26)
        assert r.content_type_hint == "video"

    # nessuna reference spuria dalla cella data stessa o dalle etichette DONE
    assert all(_.upper() != "DONE" for r in refs for _ in [r.url[:4]])


def test_righe_senza_header_categoria_precedente_vengono_ignorate():
    rows = [
        ["https://www.instagram.com/p/ORFANO/", "", "", ""],
    ]

    refs = parse_rows(rows, tab_name="CAROSELLI", year=YEAR)

    assert refs == []


def test_nomi_mese_italiani_riconosciuti_come_quelli_inglesi():
    # osservato sullo sheet reale: la prima settimana di VIRAL GENERAL usa
    # "15-21 GIUGNO" mentre settimane successive usano l'inglese ("JULY")
    rows = [
        ["OTHER CONTENTS", "", "", "", "", "BALLETTI/LIPSYNC", "", "", "", "", "TALKING", "", "", "", "", "CAPTION"],
        ["15-21 GIUGNO", "DONE NICO", "DONE RICKY", "DONE ANDRE", "DONE MATTE", "", "", "", "", "", "", "", "", "", "", ""],
        ["https://www.instagram.com/reel/ITA1/", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ]

    refs = parse_rows(rows, tab_name="VIRAL GENERAL", year=YEAR)

    assert len(refs) == 1
    assert refs[0].week_start == dt.date(2026, 6, 15)
    assert refs[0].week_end == dt.date(2026, 6, 21)


def test_caption_e_categoria_video_valida():
    rows = [
        ["CAPTION", "", ""],
        ["20-26 JULY", "DONE NICO", "DONE RICKY"],
        ["https://www.instagram.com/reel/CAP1/", "", ""],
    ]

    refs = parse_rows(rows, tab_name="VIRAL GENERAL", year=YEAR)

    assert len(refs) == 1
    assert refs[0].source_category == "CAPTION"
    assert refs[0].content_type_hint == "video"
    assert refs[0].done_ricky_col == 3
