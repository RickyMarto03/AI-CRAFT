"""Selezione delle foto di un carosello da ricreare (workflow Ruby2/Soul).

Regola (definita dall'utente): al massimo 3 foto per carosello. Se il
carosello ne ha 3 o meno, si prendono tutte. Se ne ha di piu', si prende
quella su cui atterra il link condiviso + le vicine — precedente e
successiva quando entrambe disponibili, altrimenti due precedenti o due
successive a seconda di quale lato manca (bordo del carosello).

Stadio deterministico (codice puro), nessun giudizio creativo.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def parse_img_index(url: str) -> int:
    """Indice (0-based) dell'immagine su cui atterra il link.

    Il parametro `img_index` di Instagram e' 1-based; se assente, il link
    atterra sulla prima immagine del carosello per convenzione IG (indice 0).
    """
    query = parse_qs(urlparse(url).query)
    raw = query.get("img_index", ["1"])[0]
    try:
        one_based = int(raw)
    except ValueError:
        one_based = 1
    return max(0, one_based - 1)


def select_carousel_indices(total_count: int, landing_index: int, *, max_photos: int = 3) -> list:
    """Indici (0-based, in ordine) delle foto da ricreare.

    Finestra di `max_photos` centrata su `landing_index`, clampata ai bordi
    del carosello — cosi' vicino a un bordo si sposta automaticamente verso
    "due successive" o "due precedenti" invece di uscire dal range.
    """
    if total_count <= 0:
        return []
    if total_count <= max_photos:
        return list(range(total_count))

    landing_index = max(0, min(landing_index, total_count - 1))
    half = max_photos // 2
    start = landing_index - half
    start = max(0, min(start, total_count - max_photos))
    return list(range(start, start + max_photos))


def select_carousel_photos(image_paths: list, source_url: str, *, max_photos: int = 3) -> list:
    """Applica la selezione a una lista di path gia' scaricati (in ordine),
    usando l'img_index ricavato dall'URL originale del post."""
    landing_index = parse_img_index(source_url)
    indices = select_carousel_indices(len(image_paths), landing_index, max_photos=max_photos)
    return [image_paths[i] for i in indices]
