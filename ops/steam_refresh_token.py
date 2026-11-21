#!/usr/bin/env python3
"""Comprueba un refresh token de Steam antes de confiarle el job diario.

    .venv/bin/python ops/steam_refresh_token.py

Usa el STEAM_REFRESH_TOKEN del .env si ya está puesto; si no, lo pide por
consola (no se ve al escribir, no se guarda y no se imprime). Dice cuándo
caduca, pide a Steam un token del día y lee con él la biblioteca familiar, así
que si acaba en ✓ el job diario funcionará.

Para sacar el token: con la sesión de Steam iniciada en el navegador, abre
login.steampowered.com/jwt/ajaxrefresh → DevTools (⌥⌘I) → Application → Cookies
→ origen `login.steampowered.com` → `steamRefresh_steam`, y copia el valor
entero («<steamid>||<token>»: se admite tal cual). Ojo: en store. y en help.
no está; lo que hay allí es `steamLoginSecure`, que es un token de acceso corto
y no sirve.
"""
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from export import (access_token, clean_refresh_token,  # noqa: E402
                    refresh_token_expiry, shared_games)


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
    token = clean_refresh_token(raw)
    if not (token and steamid):
        sys.exit("Faltan STEAM_ID o el refresh token: nada que comprobar.")

    expiry = refresh_token_expiry(token)
    print(f"  caduca el {expiry}" if expiry else "  ⚠ no parece un JWT; lo pruebo igual")
    try:
        day_token = access_token(token, steamid)
    except Exception as e:
        sys.exit(f"✗ Steam no da un token del día con ese refresh token: {e}")
    print("✓ Steam da un token del día")
    try:
        apps = shared_games(day_token, steamid)
    except Exception as e:
        sys.exit(f"✗ El token del día no sirve para la biblioteca familiar: {e}")
    print(f"✓ Biblioteca familiar: {len(apps)} apps")
    print("\nPonlo en el .env del Mac como STEAM_REFRESH_TOKEN=… y lanza scripts/daily.sh.")


if __name__ == "__main__":
    main()
