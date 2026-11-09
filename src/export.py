#!/usr/bin/env python3
"""
Exporta la biblioteca de Steam (propia + compartida por Family Sharing) a
data/raw/steam_library.csv y data/raw/steam_library.json.

Solo librería estándar (Python 3.8+). python-dotenv es opcional: si está
instalado se lee .env; si no, se usan las variables de entorno.

Claves (en .env o en el entorno; ver .env.example):
  STEAM_API_KEY        — steamcommunity.com/dev/apikey
  STEAM_ID             — SteamID64 (17 dígitos, empieza por 7656)
  STEAM_WEBAPI_TOKEN   — opcional, para la biblioteca compartida (caduca ~24 h)

Si falta alguna clave, se pide por consola (no se guarda en ningún fichero).
"""
import csv
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.steampowered.com"
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def get(path, params):
    url = f"{API}/{path}/?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "steam-library-export"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ts(x):
    if not x:
        return ""
    return datetime.fromtimestamp(int(x), tz=timezone.utc).strftime("%Y-%m-%d")


def owned_games(key, steamid):
    data = get("IPlayerService/GetOwnedGames/v1", {
        "key": key, "steamid": steamid,
        "include_appinfo": 1, "include_played_free_games": 1,
    })
    games = data.get("response", {}).get("games", [])
    if not games:
        print("  ⚠ 0 juegos propios. ¿Perfil y 'Detalles de juegos' en Público?")
    out = {}
    for g in games:
        out[g["appid"]] = {
            "appid": g["appid"],
            "name": g.get("name", ""),
            "source": "propio",
            "playtime_hours": round(g.get("playtime_forever", 0) / 60, 1),
            "playtime_2weeks_hours": round(g.get("playtime_2weeks", 0) / 60, 1),
            "last_played": ts(g.get("rtime_last_played")),
            "acquired": "",
            "owners": steamid,
        }
    return out


def shared_games(token, steamid):
    fam = get("IFamilyGroupsService/GetFamilyGroupForUser/v1",
              {"access_token": token, "steamid": steamid})
    gid = fam.get("response", {}).get("family_groupid")
    if not gid:
        print("  ⚠ No encuentro grupo familiar para ese SteamID.")
        return {}
    data = get("IFamilyGroupsService/GetSharedLibraryApps/v1", {
        "access_token": token, "family_groupid": gid, "steamid": steamid,
        "include_own": 1, "include_excluded": 1, "include_free": 1,
        "include_non_games": 0, "language": "spanish",
    })
    apps = data.get("response", {}).get("apps", [])
    out = {}
    for a in apps:
        owners = [str(o) for o in a.get("owner_steamids", [])]
        out[a["appid"]] = {
            "appid": a["appid"],
            "name": a.get("name", ""),
            "source": "propio" if steamid in owners else "compartido",
            "playtime_hours": round(int(a.get("rt_playtime", 0) or 0) / 60, 1),
            "playtime_2weeks_hours": "",
            "last_played": ts(a.get("rt_last_played")),
            "acquired": ts(a.get("rt_time_acquired")),
            "owners": ";".join(owners),
            "excluded_reason": a.get("exclude_reason", ""),
        }
    return out


def read_previous(csv_path):
    if not Path(csv_path).exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def keep_previous(lib, previous):
    """Sin biblioteca compartida (token caducado), no se pierde lo que ya se sabía.

    Los juegos que solo venían de la API familiar se conservan del export anterior
    (con sus horas de entonces) y los propios recuperan la fecha de adquisición y
    los dueños, que solo da esa API. Devuelve cuántos juegos se han conservado.
    """
    kept = 0
    for row in previous:
        appid = row.get("appid")
        if not appid:
            continue
        if appid in lib:
            for field in ("acquired", "owners"):
                if not lib[appid].get(field) and row.get(field):
                    lib[appid][field] = row[field]
        else:
            lib[appid] = row
            kept += 1
    return kept


def setting(name, prompt, secret=True, required=True):
    """Lee la variable del entorno; solo pregunta por consola si hay terminal."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if sys.stdin.isatty():
        return (getpass.getpass(prompt) if secret else input(prompt)).strip()
    if required:
        sys.exit(f"Falta {name} en .env")
    return ""


def main():
    key = setting("STEAM_API_KEY", "API key (no se ve al escribir): ")
    steamid = setting("STEAM_ID", "SteamID64: ", secret=False)
    token = setting("STEAM_WEBAPI_TOKEN",
                    "webapi_token para biblioteca compartida (Enter para omitir): ", required=False)
    if not token:
        print("  ⚠ Sin STEAM_WEBAPI_TOKEN: omito la biblioteca compartida.")

    print("→ Juegos propios…")
    lib = {str(k): v for k, v in owned_games(key, steamid).items()}
    print(f"  {len(lib)} juegos")
    csv_path = RAW_DIR / "steam_library.csv"
    shared_ok, shared_error = False, "sin STEAM_WEBAPI_TOKEN" if not token else ""

    if token:
        print("→ Biblioteca compartida…")
        try:
            shared = {str(k): v for k, v in shared_games(token, steamid).items()}
            print(f"  {len(shared)} apps en el grupo familiar")
            shared_ok = bool(shared)
            for appid, s in shared.items():
                if appid in lib:
                    # Conserva horas exactas de GetOwnedGames, añade fecha de adquisición/dueños
                    lib[appid]["acquired"] = s["acquired"]
                    lib[appid]["owners"] = s["owners"]
                else:
                    lib[appid] = s
        except Exception as e:
            print(f"  ⚠ Falló la biblioteca compartida ({e}). ¿Caducó STEAM_WEBAPI_TOKEN?")
            shared_error = str(e)

    if not shared_ok:
        kept = keep_previous(lib, read_previous(csv_path))
        if kept:
            print(f"  ↺ Conservo {kept} juegos y las fechas de compra del export anterior.")

    rows = sorted(lib.values(), key=lambda r: r["name"].lower())
    fields = ["appid", "name", "source", "playtime_hours", "playtime_2weeks_hours",
              "last_played", "acquired", "owners", "excluded_reason"]

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RAW_DIR / "steam_library.json"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    # Para avisar por Telegram (notify.py) de que hay que renovar el token.
    with open(RAW_DIR / "export_status.json", "w", encoding="utf-8") as f:
        json.dump({"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "shared_ok": shared_ok,
                   "shared_error": shared_error}, f)

    n_shared = sum(1 for r in rows if r["source"] == "compartido")
    print(f"\n✓ {len(rows)} juegos ({len(rows) - n_shared} propios, {n_shared} compartidos)")
    print(f"  → {csv_path} / {json_path}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        sys.exit(f"Error HTTP {e.code}: revisa la API key / SteamID / token.")
