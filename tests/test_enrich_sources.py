import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from enrich_sources import (SourceError, clean_name, parse_items, parse_store, parse_tag_list,
                            pick_hltb)

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_parse_store_real_fixture():
    out = parse_store(load("store_1145360.json"), "1145360")
    assert out["genres"] and all(isinstance(g, str) for g in out["genres"])
    assert all(isinstance(c, str) for c in out["categories"])
    assert out["metacritic"] is None or isinstance(out["metacritic"], int)


def test_parse_store_delisted():
    assert parse_store({"1": {"success": False}}, "1") == {
        "genres": [], "categories": [], "metacritic": None}


def tag_names():
    return parse_tag_list(load("taglist_subset.json"))


def test_parse_tag_list():
    names = tag_names()
    assert names[19] == "Acción"
    assert all(isinstance(k, int) and isinstance(v, str) for k, v in names.items())


def test_parse_items_real_fixture():
    out = parse_items(load("items_1145360.json"), tag_names())
    assert len(out["tags"]) > 5
    assert all(isinstance(v, int) for v in out["tags"].values())
    assert "Roguelike" in out["tags"]
    assert 0 <= out["reviews_positive"] <= out["reviews_total"]
    assert out["reviews_total"] > 1000


def test_parse_items_skips_unknown_tagids():
    payload = {"response": {"store_items": [{"success": 1, "tags": [
        {"tagid": 19, "weight": 10}, {"tagid": 999999, "weight": 5}]}]}}
    assert parse_items(payload, {19: "Acción"})["tags"] == {"Acción": 10}


def test_parse_items_without_tags_or_reviews():
    payload = {"response": {"store_items": [{"success": 1}]}}
    assert parse_items(payload, {}) == {"tags": {}, "reviews_positive": None, "reviews_total": None}


def test_parse_items_not_found_is_empty_not_error():
    payload = {"response": {"store_items": [{"id": 1, "success": 2}]}}
    assert parse_items(payload, {}) == {"tags": {}, "reviews_positive": None, "reviews_total": None}


def test_parse_items_bad_payload_raises():
    with pytest.raises(SourceError):
        parse_items({"response": {}}, {})


def test_clean_name():
    assert clean_name("ACE COMBAT™7: SKIES  UNKNOWN") == "ACE COMBAT 7: SKIES UNKNOWN"
    assert clean_name("Portal® 2 ©") == "Portal 2"


def entry(name, similarity, main=10.0, extra=15.0):
    return SimpleNamespace(game_name=name, similarity=similarity, main_story=main, main_extra=extra)


def test_pick_hltb_best_similarity():
    out = pick_hltb("Hades", [entry("Hades II", 0.7, 30, 40), entry("Hades", 1.0, 22, 49)])
    assert out == {"hltb_main": 22, "hltb_extra": 49}


def test_pick_hltb_below_threshold():
    assert pick_hltb("Hades", [entry("Otro", 0.5)]) == {"hltb_main": None, "hltb_extra": None}
    assert pick_hltb("Hades", []) == {"hltb_main": None, "hltb_extra": None}


def test_pick_hltb_zero_times_are_none():
    assert pick_hltb("Hades", [entry("Hades", 1.0, 0, 12)]) == {"hltb_main": None, "hltb_extra": 12}


def test_fetch_hltb_is_case_insensitive_and_cleans_name(monkeypatch):
    calls = []

    class FakeHLTB:
        def search(self, name, **kw):
            calls.append((name, kw))
            return [entry("Ace Combat 7: Skies Unknown", 1.0, 12.27, 20.0)]

    import howlongtobeatpy
    import enrich_sources
    monkeypatch.setattr(howlongtobeatpy, "HowLongToBeat", FakeHLTB)
    monkeypatch.setattr(enrich_sources._HLTB, "interval", 0)
    out = enrich_sources.fetch_hltb("502500", "ACE COMBAT™7: SKIES UNKNOWN")
    assert calls == [("ACE COMBAT 7: SKIES UNKNOWN", {"similarity_case_sensitive": False})]
    assert out == {"hltb_main": 12.27, "hltb_extra": 20.0}


def test_fetch_hltb_failed_request_raises(monkeypatch):
    class FakeHLTB:
        def search(self, name, **kw):
            return None

    import howlongtobeatpy
    import enrich_sources
    monkeypatch.setattr(howlongtobeatpy, "HowLongToBeat", FakeHLTB)
    monkeypatch.setattr(enrich_sources._HLTB, "interval", 0)
    with pytest.raises(SourceError):
        enrich_sources.fetch_hltb("1", "x")
