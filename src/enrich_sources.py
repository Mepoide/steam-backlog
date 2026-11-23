"""Fuentes de datos de Steam: tienda (géneros), IStoreBrowseService (tags con
peso, reseñas, imágenes y fecha de salida), IWishlistService (lista de deseos) y
HowLongToBeat (duración).

Los parse_* son puros (testeables con respuestas grabadas); los fetch_* hacen la
petición HTTP respetando un intervalo mínimo por servicio.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "steam-backlog"
HLTB_MIN_SIMILARITY = 0.6

STORE_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&l=spanish&cc=es"
ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json={q}"
TAGLIST_URL = "https://api.steampowered.com/IStoreService/GetTagList/v1/?language=spanish"
WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/?steamid={steamid}"
ITEMS_TAG_COUNT = 20
ITEMS_BATCH = 50  # appids por petición de GetItems al pedir varias fichas a la vez
ASSETS_CDN = "https://shared.akamai.steamstatic.com/store_item_assets/"
ICON_CDN = "https://shared.akamai.steamstatic.com/community_assets/images/apps/{appid}/{icon}.jpg"
# Nombre que usamos → campo de `assets` en GetItems. Tamaños: header 460×215,
# capsule 616×353, small 231×87, portrait 600×900 (biblioteca), hero 1920×620.
ASSET_FIELDS = {"header": "header", "capsule": "main_capsule", "small": "small_capsule",
                "portrait": "library_capsule", "hero": "library_hero"}
# platforms.steam_deck_compat_category de GetItems.
DECK_UNKNOWN, DECK_UNSUPPORTED, DECK_PLAYABLE, DECK_VERIFIED = 0, 1, 2, 3


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


def parse_assets(item: dict) -> dict[str, str]:
    """URLs absolutas de las imágenes de la tienda (cabecera, cápsulas, portada, hero, icono)."""
    assets = item.get("assets") or {}
    fmt, out = assets.get("asset_url_format"), {}
    if fmt and "${FILENAME}" in fmt:
        for name, field in ASSET_FIELDS.items():
            if assets.get(field):
                out[name] = ASSETS_CDN + fmt.replace("${FILENAME}", assets[field])
    if assets.get("community_icon") and item.get("appid"):
        out["icon"] = ICON_CDN.format(appid=item["appid"], icon=assets["community_icon"])
    return out


def parse_items(payload: dict, tag_names: dict[int, str]) -> dict:
    try:
        item = payload["response"]["store_items"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise SourceError("GetItems inesperado") from e
    out = {"tags": {}, "reviews_positive": None, "reviews_total": None, "images": {},
           "deck": None, "early_access": False, "coming_soon": False, "release_date": None}
    if item.get("success") != 1:
        return out  # appid inexistente o retirado: no es un fallo reintentable
    for t in item.get("tags") or []:
        name = tag_names.get(int(t["tagid"]))
        if name:
            out["tags"][name] = int(t.get("weight", 0))
    out.update(parse_reviews(item))
    out["images"] = parse_assets(item)
    out.update(parse_release(item))
    return out


def parse_reviews(item: dict) -> dict:
    """Reseñas de un item de GetItems: total y cuántas son positivas."""
    summary = (item.get("reviews") or {}).get("summary_filtered")
    if not summary or summary.get("review_count") is None:
        return {"reviews_positive": None, "reviews_total": None}
    total = int(summary["review_count"])
    return {"reviews_total": total,
            "reviews_positive": round(total * int(summary.get("percent_positive", 0)) / 100)}


def _date(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat() if ts else None


def parse_release(item: dict) -> dict:
    """Compatibilidad con Steam Deck, acceso anticipado y fecha de salida en Steam."""
    release = item.get("release") or {}
    deck = (item.get("platforms") or {}).get("steam_deck_compat_category")
    return {
        "deck": int(deck) if deck is not None else None,
        "early_access": bool(item.get("is_early_access") or release.get("is_early_access")),
        "coming_soon": bool(item.get("is_coming_soon") or release.get("is_coming_soon")),
        "release_date": _date(release.get("steam_release_date")),
    }


def parse_item_cards(payload: dict) -> dict[str, dict]:
    """Ficha corta (nombre, reseñas, imágenes y fecha de salida) por appid.

    Es lo que hace falta de los juegos que NO están en la biblioteca, como los de
    la lista de deseos: ahí no hay horas jugadas ni perfil de gusto que puntuar,
    así que no se piden tags ni duración.
    """
    try:
        items = payload["response"]["store_items"]
    except (KeyError, TypeError) as e:
        raise SourceError("GetItems inesperado") from e
    out = {}
    for item in items or []:
        appid = item.get("appid") or item.get("id")
        if item.get("success") != 1 or not appid:
            continue  # retirado o inexistente: se queda sin ficha, no es un fallo
        card = {"name": item.get("name") or "", **parse_reviews(item), "images": parse_assets(item),
                **parse_release(item)}
        out[str(appid)] = card
    return out


def parse_wishlist(payload: dict) -> list[dict]:
    """Lista de deseos de GetWishlist: [{appid, priority, added}], en el orden de Steam.

    Una respuesta sin `items` no es un fallo: es lo que devuelve Steam cuando la
    lista está vacía y también cuando el perfil no la comparte.
    """
    try:
        items = (payload["response"] or {}).get("items") or []
    except (KeyError, TypeError) as e:
        raise SourceError("GetWishlist inesperado") from e
    out = []
    for item in items:
        appid = item.get("appid")
        if appid is None:
            continue
        out.append({"appid": str(appid), "priority": int(item.get("priority") or 0),
                    "added": _date(item.get("date_added"))})
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


def items_url(appids) -> str:
    q = json.dumps({
        "ids": [{"appid": int(a)} for a in appids],
        "context": {"language": "spanish", "country_code": "ES"},
        "data_request": {"include_tag_count": ITEMS_TAG_COUNT, "include_reviews": True,
                         "include_assets": True, "include_platforms": True,
                         "include_release": True},
    }, separators=(",", ":"))
    return ITEMS_URL.format(q=urllib.parse.quote(q))


def fetch_items(appid: str, name: str) -> dict:
    return parse_items(_get_json(items_url([appid]), _STEAM_API), _tag_names())


def fetch_item_cards(appids) -> dict[str, dict]:
    """Fichas cortas de varios juegos, en lotes (una petición por lote de ITEMS_BATCH)."""
    appids = [str(a) for a in appids]
    out = {}
    for i in range(0, len(appids), ITEMS_BATCH):
        out.update(parse_item_cards(_get_json(items_url(appids[i:i + ITEMS_BATCH]), _STEAM_API)))
    return out


def fetch_wishlist(steamid: str) -> list[dict]:
    """La lista de deseos del perfil. Pública: no lleva API key ni token."""
    return parse_wishlist(_get_json(WISHLIST_URL.format(steamid=steamid), _STEAM_API))


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
