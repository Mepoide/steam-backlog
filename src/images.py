#!/usr/bin/env python3
"""Imágenes de los juegos: las URLs vienen de la tienda (enrich.py → games.json, campo
"images") y aquí se descargan a una caché local para la web.

    python src/images.py [--kinds header,portrait,icon] [--limit N]
        → data/images/<appid>/<tipo>.jpg (solo las que falten)

Tipos: header 460×215 (cabecera), capsule 616×353, small 231×87, portrait 600×900
(portada de biblioteca), hero 1920×620 (fondo) e icon 32×32. El bot no descarga
nada: Telegram las pide directamente a la CDN de Steam.

Las imágenes son de Steam y de cada editora: viven en data/ (ignorado), no en git.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import GAMES_JSON, IMAGES_DIR

KINDS = ("header", "capsule", "small", "portrait", "hero", "icon")
DEFAULT_KINDS = ("header", "portrait", "icon")
INTERVAL = 0.2  # entre descargas: es una CDN, pero sin abusar
UA = "steam-backlog"


def image_url(entry: dict, kind: str = "header") -> str | None:
    """URL de la imagen `kind` del juego, o None si la tienda no la tiene."""
    return (entry.get("images") or {}).get(kind) or None


def local_path(appid: str, kind: str, root: Path = IMAGES_DIR) -> Path:
    return Path(root) / str(appid) / f"{kind}.jpg"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def download_missing(games: dict, kinds=DEFAULT_KINDS, root: Path = IMAGES_DIR, fetch=_fetch,
                     limit: int | None = None, sleep=time.sleep) -> tuple[int, int]:
    """Descarga las imágenes que falten. Devuelve (descargadas, fallidas)."""
    done = failed = 0
    for appid, entry in games.items():
        for kind in kinds:
            url, path = image_url(entry, kind), local_path(appid, kind, root)
            if url is None or path.exists():
                continue
            if limit is not None and done + failed >= limit:
                return done, failed
            try:
                data = fetch(url)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"  ⚠ {appid} {kind}: {e}")
                failed += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            done += 1
            sleep(INTERVAL)
    return done, failed


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Descarga las imágenes de los juegos a data/images/")
    ap.add_argument("--kinds", default=",".join(DEFAULT_KINDS),
                    help=f"tipos separados por comas ({', '.join(KINDS)})")
    ap.add_argument("--limit", type=int, help="descarga como mucho N imágenes")
    args = ap.parse_args(argv)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    if unknown := [k for k in kinds if k not in KINDS]:
        sys.exit(f"Tipos desconocidos: {', '.join(unknown)}")
    if not GAMES_JSON.exists():
        sys.exit(f"No existe {GAMES_JSON}. Ejecuta antes enrich.py.")
    games = json.loads(GAMES_JSON.read_text(encoding="utf-8"))
    done, failed = download_missing(games, kinds, limit=args.limit)
    with_images = sum(1 for e in games.values() if e.get("images"))
    print(f"✓ {done} imágenes descargadas, {failed} fallidas ({with_images} juegos con imágenes)")


if __name__ == "__main__":
    main()
