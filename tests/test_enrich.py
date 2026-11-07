from collections import Counter

from enrich import SOURCES, enrich_all
from enrich_sources import RateLimited, SourceError

STORE = {"genres": ["Acción"], "categories": ["Un jugador"], "metacritic": 90}
ITEMS = {"tags": {"Roguelike": 100}, "reviews_positive": 90, "reviews_total": 100}
HLTB = {"hltb_main": 20.0, "hltb_extra": 30.0}


class Fakes:
    """Fetchers falsos que cuentan llamadas y pueden fallar a demanda."""

    def __init__(self, fail=None):
        self.calls = Counter()
        self.fail = fail or {}  # source -> excepción (o callable(appid) -> excepción|None)

    def make(self, source, result):
        def fetch(appid, name):
            self.calls[(source, appid)] += 1
            err = self.fail.get(source)
            if callable(err):
                err = err(appid)
            if err:
                raise err
            return dict(result)
        return fetch

    def fetchers(self):
        return {"store": self.make("store", STORE), "items": self.make("items", ITEMS),
                "hltb": self.make("hltb", HLTB)}

    def total(self, source=None):
        return sum(v for (s, _), v in self.calls.items() if source in (None, s))


def test_sources_are_store_items_hltb():
    assert SOURCES == ("store", "items", "hltb")


def test_new_game_gets_all_sources_merged():
    f = Fakes()
    cache = enrich_all({"1": "Uno"}, {}, f.fetchers())
    assert cache["1"] == {"name": "Uno", **STORE, **ITEMS, **HLTB, "missing": []}


def test_complete_cached_entry_not_refetched():
    f = Fakes()
    cache = enrich_all({"1": "Uno"}, {}, f.fetchers())
    f2 = Fakes()
    enrich_all({"1": "Uno"}, cache, f2.fetchers())
    assert f2.total() == 0


def test_failed_source_marked_missing_and_retried_alone():
    f = Fakes(fail={"items": SourceError("caído")})
    cache = enrich_all({"1": "Uno"}, {}, f.fetchers())
    assert cache["1"]["missing"] == ["items"]
    assert cache["1"]["tags"] == {} and cache["1"]["genres"] == ["Acción"]

    f2 = Fakes()
    cache = enrich_all({"1": "Uno"}, cache, f2.fetchers())
    assert f2.total() == f2.total("items") == 1
    assert cache["1"]["missing"] == []
    assert cache["1"]["tags"] == {"Roguelike": 100}


def test_rate_limited_source_skipped_for_rest_of_run():
    f = Fakes(fail={"store": lambda appid: RateLimited("429") if appid == "1" else None})
    cache = enrich_all({"1": "Uno", "2": "Dos"}, {}, f.fetchers())
    assert f.calls[("store", "2")] == 0
    assert "store" in cache["1"]["missing"] and "store" in cache["2"]["missing"]
    assert cache["2"]["tags"] == {"Roguelike": 100}


def test_not_found_is_not_missing():
    f = Fakes()
    fetchers = f.fetchers()
    fetchers["hltb"] = lambda appid, name: {"hltb_main": None, "hltb_extra": None}
    cache = enrich_all({"1": "Uno"}, {}, fetchers)
    assert cache["1"]["missing"] == []
    assert cache["1"]["hltb_main"] is None


def test_refresh_refetches_everything():
    f = Fakes()
    cache = enrich_all({"1": "Uno"}, {}, f.fetchers())
    f2 = Fakes()
    enrich_all({"1": "Uno"}, cache, f2.fetchers(), refresh=True)
    assert f2.total() == 3


def test_limit_caps_processed_games():
    f = Fakes()
    cache = enrich_all({str(i): f"G{i}" for i in range(5)}, {}, f.fetchers(), limit=2)
    assert len(cache) == 2


def test_save_called_every_n_and_at_end():
    f, saves = Fakes(), []
    enrich_all({str(i): f"G{i}" for i in range(5)}, {}, f.fetchers(),
               save=lambda c: saves.append(len(c)), save_every=2)
    assert saves == [2, 4, 5]


def test_name_updated_from_library():
    f = Fakes()
    cache = enrich_all({"1": "Uno"}, {}, f.fetchers())
    cache = enrich_all({"1": "Uno (renombrado)"}, cache, Fakes().fetchers())
    assert cache["1"]["name"] == "Uno (renombrado)"
