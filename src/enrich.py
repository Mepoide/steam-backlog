#!/usr/bin/env python3
"""
Enriquece la biblioteca exportada con géneros, tags, reseñas y duración.

Lee data/raw/steam_library.csv y mantiene data/enriched/games.json (caché por
appid). Solo pide lo que falta: juegos nuevos y fuentes que fallaron antes
(campo "missing"). Un "no encontrado" no es un fallo y no se reintenta.

    python src/enrich.py [--refresh] [--limit N]
"""
import argparse
import csv
import json
import sys
from typing import Callable

from config import GAMES_JSON, RAW_CSV
from enrich_sources import RateLimited, SourceError, fetch_hltb, fetch_items, fetch_store
from library import atomic_write_json

SOURCES = ("store", "items", "hltb")
EMPTY = {
    "genres": [], "categories": [], "metacritic": None,
    "tags": {}, "reviews_positive": None, "reviews_total": None, "images": {},
    "hltb_main": None, "hltb_extra": None,
}


def _new_entry(name: str) -> dict:
    return {"name": name, **json.loads(json.dumps(EMPTY)), "missing": list(SOURCES)}


def enrich_all(games: dict[str, str], cache: dict,
               fetchers: dict[str, Callable[[str, str], dict]],
               refresh: bool = False, limit: int | None = None,
               save: Callable[[dict], None] = lambda c: None, save_every: int = 25) -> dict:
    blocked: set[str] = set()
    processed = 0
    for appid, name in games.items():
        entry = cache.get(appid)
        if entry is not None:
            entry["name"] = name
            if "images" not in entry and "items" not in entry["missing"]:
                entry["missing"].append("items")  # cachés de antes de las imágenes: se completan
        needs_work = entry is None or refresh or bool(entry["missing"])
        if not needs_work:
            continue
        if limit is not None and processed >= limit:
            break
        if entry is None or refresh:
            entry = _new_entry(name)
        cache[appid] = entry
        still_missing = []
        for source in entry["missing"]:
            if source in blocked:
                still_missing.append(source)
                continue
            try:
                entry.update(fetchers[source](appid, name))
            except RateLimited as e:
                print(f"  ⚠ {source}: límite de peticiones ({e}); la dejo para la próxima")
                blocked.add(source)
                still_missing.append(source)
            except SourceError as e:
                print(f"  ⚠ {source} {appid} {name}: {e}")
                still_missing.append(source)
        entry["missing"] = still_missing
        processed += 1
        if processed % save_every == 0:
            save(cache)
    save(cache)
    return cache


def read_library(path=RAW_CSV) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8") as f:
        return {row["appid"]: row.get("name", "") for row in csv.DictReader(f)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="vuelve a pedir todo")
    ap.add_argument("--limit", type=int, help="procesa como mucho N juegos")
    args = ap.parse_args(argv)

    if not RAW_CSV.exists():
        sys.exit(f"No existe {RAW_CSV}. Ejecuta antes src/export.py.")
    cache = json.loads(GAMES_JSON.read_text(encoding="utf-8")) if GAMES_JSON.exists() else {}
    fetchers = {"store": fetch_store, "items": fetch_items, "hltb": fetch_hltb}
    cache = enrich_all(read_library(), cache, fetchers, refresh=args.refresh, limit=args.limit,
                       save=lambda c: atomic_write_json(GAMES_JSON, c))
    pending = sum(1 for e in cache.values() if e["missing"])
    print(f"✓ {len(cache) - pending} juegos enriquecidos, {pending} con fuentes pendientes")


if __name__ == "__main__":
    main()
