"""Fuentes de datos por juego: tienda de Steam (géneros), IStoreBrowseService
(tags con peso y reseñas) y HowLongToBeat (duración).

Los parse_* son puros (testeables con respuestas grabadas); los fetch_* hacen la
petición HTTP respetando un intervalo mínimo por servicio.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "steam-backlog"
HLTB_MIN_SIMILARITY = 0.6

STORE_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&l=spanish&cc=es"
ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json={q}"
TAGLIST_URL = "https://api.steampowered.com/IStoreService/GetTagList/v1/?language=spanish"
ITEMS_TAG_COUNT = 20


class SourceError(Exception):
    """Fallo de red, HTTP o respuesta inválida: la fuente se reintentará."""


class RateLimited(SourceError):
    """HTTP 429: no seguir preguntando a esta fuente en esta ejecución."""


class Throttle:
    def __init__(self, interval: float):
        self.interval = interval
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


_STEAM_STORE = Throttle(1.5)
_STEAM_API = Throttle(1.0)
_HLTB = Throttle(1.0)
_tag_names_cache: dict[int, str] | None = None


def _get_json(url: str, throttle: Throttle):
    throttle.wait()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(f"HTTP 429 en {url}") from e
        raise SourceError(f"HTTP {e.code} en {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise SourceError(f"{type(e).__name__} en {url}: {e}") from e


def parse_store(payload: dict, appid: str) -> dict:
    if not isinstance(payload, dict) or str(appid) not in payload:
        raise SourceError("respuesta de appdetails vacía o inesperada")
    item = payload[str(appid)] or {}
    data = item.get("data") if item.get("success") else None
    if not data:
        return {"genres": [], "categories": [], "metacritic": None}
    score = (data.get("metacritic") or {}).get("score")
    return {
        "genres": [g["description"] for g in data.get("genres", []) if g.get("description")],
        "categories": [c["description"] for c in data.get("categories", []) if c.get("description")],
        "metacritic": int(score) if score is not None else None,
    }


def parse_tag_list(payload: dict) -> dict[int, str]:
    try:
        return {int(t["tagid"]): t["name"] for t in payload["response"]["tags"]}
    except (KeyError, TypeError, ValueError) as e:
        raise SourceError("GetTagList inesperado") from e


def parse_items(payload: dict, tag_names: dict[int, str]) -> dict:
    try:
        item = payload["response"]["store_items"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise SourceError("GetItems inesperado") from e
    out = {"tags": {}, "reviews_positive": None, "reviews_total": None}
    if item.get("success") != 1:
        return out  # appid inexistente o retirado: no es un fallo reintentable
    for t in item.get("tags") or []:
        name = tag_names.get(int(t["tagid"]))
        if name:
            out["tags"][name] = int(t.get("weight", 0))
    summary = (item.get("reviews") or {}).get("summary_filtered")
    if summary and summary.get("review_count") is not None:
        total = int(summary["review_count"])
        out["reviews_total"] = total
        out["reviews_positive"] = round(total * int(summary.get("percent_positive", 0)) / 100)
    return out


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[™®©]", " ", name)).strip()


def pick_hltb(name: str, entries: list) -> dict:
    best = max(entries or [], key=lambda e: e.similarity, default=None)
    if best is None or best.similarity < HLTB_MIN_SIMILARITY:
        return {"hltb_main": None, "hltb_extra": None}
    return {"hltb_main": best.main_story or None, "hltb_extra": best.main_extra or None}


def fetch_store(appid: str, name: str) -> dict:
    return parse_store(_get_json(STORE_URL.format(appid=appid), _STEAM_STORE), appid)


def _tag_names() -> dict[int, str]:
    global _tag_names_cache
    if _tag_names_cache is None:
        _tag_names_cache = parse_tag_list(_get_json(TAGLIST_URL, _STEAM_API))
    return _tag_names_cache


def fetch_items(appid: str, name: str) -> dict:
    q = json.dumps({
        "ids": [{"appid": int(appid)}],
        "context": {"language": "spanish", "country_code": "ES"},
        "data_request": {"include_tag_count": ITEMS_TAG_COUNT, "include_reviews": True},
    }, separators=(",", ":"))
    url = ITEMS_URL.format(q=urllib.parse.quote(q))
    return parse_items(_get_json(url, _STEAM_API), _tag_names())


def fetch_hltb(appid: str, name: str) -> dict:
    from howlongtobeatpy import HowLongToBeat

    _HLTB.wait()
    try:
        entries = HowLongToBeat().search(clean_name(name), similarity_case_sensitive=False)
    except Exception as e:  # la librería no documenta sus excepciones
        raise SourceError(f"HLTB: {type(e).__name__}: {e}") from e
    if entries is None:  # la librería devuelve None si falla la petición; [] si no hay resultados
        raise SourceError("HLTB: petición fallida")
    return pick_hltb(name, entries)
