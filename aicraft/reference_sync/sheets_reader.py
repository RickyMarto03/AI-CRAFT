"""Lettura + mark controllato del Google Sheet di reference.

Il parsing e' guidato dal contenuto delle celle (nomi di categoria, pattern
di data), non da lettere di colonna fisse: i due tab noti ("CAROSELLI" e
"VIRAL GENERAL") hanno layout diversi tra loro e la posizione delle colonne
puo' spostarsi leggermente nel tempo. Vedi docs/ai-craft-architecture.md §7.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Optional

# Categoria dello sheet -> content_type_hint di ReferenceItem.
# Estendere questo dizionario se compaiono nuovi tab/categorie.
CONTENT_TYPE_BY_CATEGORY = {
    "BOOBS": "carosello",
    "BOOTY": "carosello",
    "GENERAL": "carosello",
    "OTHER CONTENTS": "video",
    "BALLETTI/LIPSYNC": "video",
    "TALKING": "video",
    "CAPTION": "video",
}

_DATE_RANGE_RE = re.compile(
    r"^\s*(\d{1,2})(?:st|nd|rd|th)?\s*[-–]\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s*$"
)

# Uno stesso sheet mischia nomi di mese inglesi e italiani tra una settimana
# e l'altra (osservato: "18-24 may", "20-26th JULY", "15-21 GIUGNO"), sia per
# esteso sia abbreviati. Case-insensitive, valori coerenti dove le chiavi
# coinciderebbero (es. "mar" -> 3 sia per "march" sia per "marzo").
_MONTH_NAMES = {
    "gennaio": 1, "gen": 1,
    "febbraio": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "aprile": 4, "apr": 4,
    "maggio": 5, "mag": 5,
    "giugno": 6, "giu": 6,
    "luglio": 7, "lug": 7,
    "agosto": 8, "ago": 8,
    "settembre": 9, "set": 9, "sett": 9,
    "ottobre": 10, "ott": 10,
    "novembre": 11, "nov": 11,
    "dicembre": 12, "dic": 12,
    "january": 1, "jan": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11,
    "december": 12, "dec": 12,
}


@dataclass(frozen=True)
class SheetReference:
    url: str
    source_tab: str
    source_category: str
    content_type_hint: Optional[str]
    week_start: Optional[dt.date]
    week_end: Optional[dt.date]
    sheet_row_id: str
    sheet_order: int
    sheet_row: int
    sheet_col: int
    done_ricky_col: Optional[int] = None


def _is_url(value: str) -> bool:
    return value.strip().lower().startswith("http")


def _parse_date_range(value: str, year: int) -> Optional[tuple[dt.date, dt.date]]:
    match = _DATE_RANGE_RE.match(value)
    if not match:
        return None

    start_day, end_day, month_name = match.groups()
    month_key = month_name.lower()
    month = _MONTH_NAMES.get(month_key) or _MONTH_NAMES.get(month_key[:3])
    if month is None:
        return None

    try:
        return (
            dt.date(year, month, int(start_day)),
            dt.date(year, month, int(end_day)),
        )
    except ValueError:
        return None


def parse_rows(rows: list[list[str]], tab_name: str, year: int) -> list[SheetReference]:
    """Estrae le reference da una singola tab, riga per riga.

    Stato mantenuto durante lo scan:
    - ``category_columns``: mappa categoria -> indice colonna, aggiornata
      ogni volta che si incontra una riga di intestazione (una o piu' celle
      che matchano un nome di categoria noto).
    - ``current_week``: ultima coppia (inizio, fine) di date trovata in una
      qualunque cella della riga corrente o precedente.
    """
    references: list[SheetReference] = []
    category_columns: dict[str, int] = {}
    done_ricky_columns: dict[str, int] = {}
    current_week: Optional[tuple[dt.date, dt.date]] = None

    for row_idx, row in enumerate(rows, start=1):
        cells = [c.strip() for c in row]
        non_empty = [(i, c) for i, c in enumerate(cells) if c]

        for _, cell_value in non_empty:
            date_range = _parse_date_range(cell_value, year)
            if date_range:
                current_week = date_range
                break

        found_categories = {
            cell_value.upper(): i
            for i, cell_value in non_empty
            if cell_value.upper() in CONTENT_TYPE_BY_CATEGORY
        }
        if found_categories:
            category_columns = found_categories
            done_ricky_columns = {}
            continue

        if category_columns:
            # Nei tab video ogni categoria ha colonne DONE subito dopo la
            # colonna URL. Memorizziamo quella di Ricky (1-based nel dataclass)
            # per poterla flaggare quando il download e' riuscito.
            upper_cells = [c.upper().replace("  ", " ") for c in cells]
            sorted_categories = sorted(category_columns.items(), key=lambda kv: kv[1])
            for idx, (category, col_idx) in enumerate(sorted_categories):
                next_col = sorted_categories[idx + 1][1] if idx + 1 < len(sorted_categories) else len(cells)
                for done_idx in range(col_idx + 1, min(next_col, len(cells))):
                    if upper_cells[done_idx] in ("DONE RICKY", "DONE RICCARDO"):
                        done_ricky_columns[category] = done_idx
                        break

        if not category_columns:
            continue

        week_start, week_end = current_week if current_week else (None, None)
        for category, col_idx in category_columns.items():
            if col_idx >= len(cells):
                continue
            value = cells[col_idx]
            if not _is_url(value):
                continue
            references.append(
                SheetReference(
                    url=value,
                    source_tab=tab_name,
                    source_category=category,
                    content_type_hint=CONTENT_TYPE_BY_CATEGORY.get(category),
                    week_start=week_start,
                    week_end=week_end,
                    sheet_row_id=f"{tab_name}!R{row_idx}C{col_idx + 1}",
                    sheet_order=len(references) + 1,
                    sheet_row=row_idx,
                    sheet_col=col_idx + 1,
                    done_ricky_col=(done_ricky_columns.get(category) + 1 if category in done_ricky_columns else None),
                )
            )

    return references


class SheetClient:
    """Wrapper gspread. Import lazy: non serve gspread
    installato per usare/testare ``parse_rows`` da solo."""

    def __init__(self, service_account_file: str, sheet_id: str):
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_key(sheet_id)

    def read_tab(self, tab_name: str) -> list[list[str]]:
        worksheet = self._spreadsheet.worksheet(tab_name)
        return worksheet.get_all_values()

    def mark_downloaded(self, ref: SheetReference, *, carousel_color: tuple[float, float, float]) -> None:
        """Segna sullo sheet che AI-CRAFT ha scaricato/acquisito la reference.

        Video: flagga DONE RICKY quando la colonna esiste.
        Caroselli: colora la cella del link, senza aggiungere colonne/righe.
        Il DB resta la fonte vera dello stato operativo.
        """
        worksheet = self._spreadsheet.worksheet(ref.source_tab)
        if ref.source_tab.upper() == "CAROSELLI" or ref.content_type_hint == "carosello":
            from gspread.utils import rowcol_to_a1

            a1 = rowcol_to_a1(ref.sheet_row, ref.sheet_col)
            red, green, blue = carousel_color
            worksheet.format(a1, {"backgroundColor": {"red": red, "green": green, "blue": blue}})
            return

        if ref.done_ricky_col is not None:
            worksheet.update_cell(ref.sheet_row, ref.done_ricky_col, "TRUE")


def fetch_references(client: SheetClient, tabs: list[str], year: int) -> list[SheetReference]:
    references: list[SheetReference] = []
    for tab_name in tabs:
        rows = client.read_tab(tab_name)
        references.extend(parse_rows(rows, tab_name=tab_name, year=year))
    return references
