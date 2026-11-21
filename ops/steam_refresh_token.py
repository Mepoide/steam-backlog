#!/usr/bin/env python3
"""Comprueba la cookie de refresco de Steam antes de confiársela al job diario.

    .venv/bin/python ops/steam_refresh_token.py

Usa el STEAM_REFRESH_TOKEN del .env si ya está puesto; si no, lo pide por
consola (no se ve al escribir, no se guarda y no se imprime). Dice cuándo
caduca, canjea la cookie por el token del día y lee con él la biblioteca
familiar, así que si acaba en ✓ el job diario funcionará.

Para sacarla: con la sesión de Steam iniciada en el navegador, abre
login.steampowered.com/jwt/refresh → DevTools (⌥⌘I) → Application → Cookies →
origen `login.steampowered.com` → `steamRefresh_steam`, y copia el valor
**entero**, tal cual, con el «<steamid>%7C%7C» de delante. Ojo: en store. y en
help. esa cookie no está; lo que hay allí es `steamLoginSecure`, que tiene la
misma pinta pero es un token de acceso corto y no sirve.
"""
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from export import (refresh_cookie, refresh_token_expiry,  # noqa: E402
                    shared_games, webapi_token)


def main() -> None:
    # export.py ya ha leído el .env al importarlo; lo que falte se pide por consola.
    steamid = os.environ.get("STEAM_ID", "").strip()
    if not steamid and sys.stdin.isatty():
        steamid = input("SteamID64: ").strip()
    raw = os.environ.get("STEAM_REFRESH_TOKEN", "")
    if raw:
        print("→ Compruebo el STEAM_REFRESH_TOKEN del .env")
    elif sys.stdin.isatty():
        raw = getpass.getpass("Cookie steamRefresh_steam (no se ve al escribir): ")
    cookie = refresh_cookie(raw)
    if not (cookie and steamid):
        sys.exit("Faltan STEAM_ID o la cookie: nada que comprobar.")

    expiry = refresh_token_expiry(cookie)
    print(f"  caduca el {expiry}" if expiry else "  ⚠ no parece un JWT; lo pruebo igual")
    try:
        day_token = webapi_token(cookie)
    except Exception as e:
        sys.exit(f"✗ Steam no da un token del día con esa cookie: {e}")
    print("✓ Steam da un token del día")
    try:
        apps = shared_games(day_token, steamid)
    except Exception as e:
        sys.exit(f"✗ El token del día no sirve para la biblioteca familiar: {e}")
    print(f"✓ Biblioteca familiar: {len(apps)} apps")
    print("\nPonlo en el .env del Mac como STEAM_REFRESH_TOKEN=… y lanza scripts/daily.sh.")


if __name__ == "__main__":
    main()
