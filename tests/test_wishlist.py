import json
import re
from pathlib import Path

import pytest

import enrich_sources
import wishlist
from enrich_sources import (SourceError, items_url, parse_item_cards, parse_reviews,
                            parse_wishlist)
from library import read_wishlist

FIX = Path(__file__).parent / "fixtures"


def test_parse_wishlist_items():
    payload = {"response": {"items": [
        {"appid": 2719310, "priority": 1, "date_added": 1762300800},
        {"appid": 1949030, "date_added": 0},
    ]}}
    assert parse_wishlist(payload) == [
        {"appid": "2719310", "priority": 1, "added": "2025-11-05"},
        {"appid": "1949030", "priority": 0, "added": None},
    ]


def test_parse_wishlist_empty_response_is_not_an_error():
    """Steam responde {"response":{}} con la lista vacía y con el perfil cerrado."""
    assert parse_wishlist({"response": {}}) == []
    assert parse_wishlist({"response": None}) == []


def test_parse_wishlist_bad_payload_raises():
    with pytest.raises(SourceError):
        parse_wishlist({})


def test_parse_item_cards_name_release_and_reviews():
    payload = {"response": {"store_items": [
        {"appid": 2719310, "success": 1, "name": "The Expanse: Osiris Reborn",
         "release": {"is_coming_soon": True},
         "platforms": {"steam_deck_compat_category": 2},
         "reviews": {"summary_filtered": {"review_count": 200, "percent_positive": 90}}},
        {"id": 7, "success": 2, "name": "Retirado"},
    ]}}
    cards = parse_item_cards(payload)
    assert set(cards) == {"2719310"}
    card = cards["2719310"]
    assert card["name"] == "The Expanse: Osiris Reborn"
    assert card["coming_soon"] is True and card["release_date"] is None
    assert card["deck"] == 2 and card["reviews_total"] == 200 and card["reviews_positive"] == 180


def test_parse_item_cards_real_fixture():
    """Respuesta real de GetItems con dos appids: uno existe y el otro no."""
    payload = json.loads((FIX / "items_cards.json").read_text(encoding="utf-8"))
    cards = parse_item_cards(payload)
    assert list(cards) == ["2050650"]
    card = cards["2050650"]
    assert card["name"] == "Resident Evil 4"
    assert card["coming_soon"] is False and card["release_date"] == "2023-03-24"
    assert 0 <= card["reviews_positive"] <= card["reviews_total"]
    assert card["deck"] == 3 and card["images"]["header"].startswith("https://")


def test_parse_item_cards_bad_payload_raises():
    with pytest.raises(SourceError):
        parse_item_cards({"response": {}})


def test_parse_reviews_without_summary():
    assert parse_reviews({}) == {"reviews_positive": None, "reviews_total": None}


def test_items_url_carries_every_appid():
    url = items_url(["5", 6])
    assert "%22appid%22%3A5" in url and "%22appid%22%3A6" in url


def test_fetch_item_cards_batches_requests(monkeypatch):
    monkeypatch.setattr(enrich_sources, "ITEMS_BATCH", 2)
    seen = []

    def fake_get(url, throttle):
        ids = [int(n) for n in re.findall(r"appid%22%3A(\d+)", url)]
        seen.append(ids)
        return {"response": {"store_items": [{"appid": i, "success": 1, "name": f"J{i}"}
                                             for i in ids]}}

    monkeypatch.setattr(enrich_sources, "_get_json", fake_get)
    cards = enrich_sources.fetch_item_cards(["1", "2", "3"])
    assert seen == [[1, 2], [3]]
    assert [c["name"] for c in cards.values()] == ["J1", "J2", "J3"]


def test_build_merges_cards_and_fills_the_missing_ones():
    items = [{"appid": "1", "priority": 1, "added": "2026-01-02"},
             {"appid": "2", "priority": 0, "added": None}]
    cards = {"1": {"name": "Uno", "coming_soon": True, "release_date": "2027-03-01"}}
    one, two = wishlist.build(items, cards)
    assert one["name"] == "Uno" and one["coming_soon"] is True
    assert one["release_date"] == "2027-03-01" and one["added"] == "2026-01-02"
    assert two["name"] == "" and two["coming_soon"] is False and two["release_date"] is None
    assert set(one) == set(two)  # mismo esquema aunque la tienda no dé la ficha


def test_main_writes_the_wishlist(tmp_path, monkeypatch, capsys):
    path = tmp_path / "wishlist.json"
    monkeypatch.setattr(wishlist, "WISHLIST_JSON", path)
    monkeypatch.setattr(wishlist, "load_env", lambda: None)
    monkeypatch.setattr(wishlist, "require_env", lambda name: "76561197960435530")
    monkeypatch.setattr(wishlist, "fetch_wishlist",
                        lambda steamid: [{"appid": "9", "priority": 1, "added": "2026-02-03"}])
    monkeypatch.setattr(wishlist, "fetch_item_cards",
                        lambda appids: {"9": {"name": "Nueve", "coming_soon": True}})
    wishlist.main([])
    assert "1 juegos en la lista de deseos, 1 por salir" in capsys.readouterr().out
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "Nueve"
    assert read_wishlist(path)["9"]["added"] == "2026-02-03"


def test_main_with_a_private_or_empty_wishlist(tmp_path, monkeypatch, capsys):
    path = tmp_path / "wishlist.json"
    monkeypatch.setattr(wishlist, "WISHLIST_JSON", path)
    monkeypatch.setattr(wishlist, "load_env", lambda: None)
    monkeypatch.setattr(wishlist, "require_env", lambda name: "7")
    monkeypatch.setattr(wishlist, "fetch_wishlist", lambda steamid: [])
    monkeypatch.setattr(wishlist, "fetch_item_cards",
                        lambda appids: pytest.fail("sin juegos no hay que pedir fichas"))
    wishlist.main([])
    assert "«Detalles de juegos» del perfil en Público" in capsys.readouterr().out
    assert json.loads(path.read_text(encoding="utf-8")) == []


def test_read_wishlist_without_file(tmp_path):
    assert read_wishlist(tmp_path / "no-existe.json") == {}


def test_parse_item_cards_coming_soon_real_fixture():
    """Respuesta real de tres juegos sin salir: con fecha al mes, «Próximamente» y «Por confirmarse»."""
    cards = parse_item_cards(json.loads((FIX / "items_coming_soon.json").read_text(encoding="utf-8")))
    dated = cards["3600000"]
    assert dated["coming_soon"] is True and dated["release_display"] == "date_month"
    assert dated["release_date"] and dated["release_note"] == ""
    assert cards["3800010"]["release_note"] == "Próximamente"
    assert cards["3900010"]["release_note"] == "Por confirmarse"
    assert cards["3900010"]["release_date"] is None
    # Sin reseñas todavía: no se cuela un «0 % 👍».
    assert all(c["reviews_total"] == 0 for c in cards.values())


def test_release_text_of_the_real_coming_soon_fixture():
    """Lo que vería el usuario con esas tres respuestas reales."""
    from commands import _release
    cards = parse_item_cards(json.loads((FIX / "items_coming_soon.json").read_text(encoding="utf-8")))
    assert _release(cards["3600000"]).startswith("Aún no ha salido (prevista para ")
    assert "-" not in _release(cards["3600000"])  # al mes, nunca una fecha ISO con día
    assert _release(cards["3800010"]) == "Aún no ha salido: Próximamente"
    assert _release(cards["3900010"]) == "Aún no ha salido: Por confirmarse"
