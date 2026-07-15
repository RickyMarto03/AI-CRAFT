from aicraft.production.carousel_selection import (
    parse_img_index,
    select_carousel_indices,
    select_carousel_photos,
)


# --- parse_img_index ---

def test_parse_img_index_presente():
    assert parse_img_index("https://www.instagram.com/p/ABC/?img_index=3") == 2  # 1-based -> 0-based


def test_parse_img_index_assente_default_prima_immagine():
    assert parse_img_index("https://www.instagram.com/p/ABC/") == 0


def test_parse_img_index_con_altri_parametri():
    assert parse_img_index("https://www.instagram.com/p/ABC/?igsh=xyz&img_index=5") == 4


def test_parse_img_index_non_numerico_fallback_prima():
    assert parse_img_index("https://www.instagram.com/p/ABC/?img_index=abc") == 0


# --- select_carousel_indices ---

def test_carosello_con_poche_foto_le_prende_tutte():
    assert select_carousel_indices(1, landing_index=0) == [0]
    assert select_carousel_indices(3, landing_index=1) == [0, 1, 2]


def test_carosello_grande_landing_in_mezzo_prende_vicine():
    # 10 foto, atterra sulla 5 (indice 4) -> precedente(3) + landing(4) + successiva(5)
    assert select_carousel_indices(10, landing_index=4) == [3, 4, 5]


def test_carosello_grande_landing_al_bordo_iniziale_prende_due_successive():
    assert select_carousel_indices(10, landing_index=0) == [0, 1, 2]


def test_carosello_grande_landing_al_bordo_finale_prende_due_precedenti():
    assert select_carousel_indices(10, landing_index=9) == [7, 8, 9]


def test_landing_index_fuori_range_viene_clampato():
    assert select_carousel_indices(5, landing_index=99) == [2, 3, 4]
    assert select_carousel_indices(5, landing_index=-3) == [0, 1, 2]


def test_max_photos_personalizzato():
    assert select_carousel_indices(10, landing_index=5, max_photos=1) == [5]


# --- select_carousel_photos ---

def test_select_carousel_photos_integra_url_e_lista():
    paths = [f"img{i}.jpg" for i in range(10)]
    url = "https://www.instagram.com/p/ABC/?img_index=1"

    selected = select_carousel_photos(paths, url)

    assert selected == ["img0.jpg", "img1.jpg", "img2.jpg"]


def test_select_carousel_photos_carosello_piccolo():
    paths = ["a.jpg", "b.jpg"]
    selected = select_carousel_photos(paths, "https://www.instagram.com/p/ABC/")
    assert selected == ["a.jpg", "b.jpg"]
