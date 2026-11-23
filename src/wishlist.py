#!/usr/bin/env python3
"""
Lista de deseos de Steam → data/raw/wishlist.json, con el nombre y la fecha de
salida de cada juego. El bot la usa en las fichas («está en tu lista de deseos
desde…») y en /deseos.

    python src/wishlist.py

`IWishlistService/GetWishlist/v1` es pública: va con el `STEAM_ID` y no necesita
API key ni token. Eso sí, Steam solo la comparte si «Detalles de juegos» del
perfil está en Público; si no, responde una lista vacía, igual que si no hubiera
nada apuntado.
"""
import sys

from config import WISHLIST_JSON, load_env, require_env
from enrich_sources import SourceError, fetch_item_cards, fetch_wishlist
from library import atomic_write_json

# Lo que se guarda de cada juego aunque la tienda no dé su ficha (retirado, sin región).
EMPTY_CARD = {"name": "", "reviews_positive": None, "reviews_total": None, "images": {},
              "deck": None, "early_access": False, "coming_soon": False, "release_date": None}


def build(items: list[dict], cards: dict[str, dict]) -> list[dict]:
    """Cada juego de la lista de deseos con su ficha de la tienda."""
    return [{**item, **EMPTY_CARD, **(cards.get(item["appid"]) or {})} for item in items]


def main(argv=None) -> None:
    load_env()
    steamid = require_env("STEAM_ID")
    print("→ Lista de deseos…")
    items = fetch_wishlist(steamid)
    if not items:
        print("  0 juegos. Si tienes cosas apuntadas, pon «Detalles de juegos» del perfil en "
              "Público: Steam no comparte la lista de deseos si no.")
    entries = build(items, fetch_item_cards(i["appid"] for i in items) if items else {})
    atomic_write_json(WISHLIST_JSON, entries)
    coming = sum(1 for e in entries if e["coming_soon"])
    print(f"✓ {len(entries)} juegos en la lista de deseos, {coming} por salir → {WISHLIST_JSON}")


if __name__ == "__main__":
    try:
        main()
    except SourceError as e:
        sys.exit(f"No pude leer la lista de deseos: {e}")
