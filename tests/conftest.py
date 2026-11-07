import csv

RAW_FIELDS = ["appid", "name", "source", "playtime_hours", "playtime_2weeks_hours",
              "last_played", "acquired", "owners", "excluded_reason"]


def make_game(name="G", tags=None, genres=None, hltb_main=None, pos=None, total=None) -> dict:
    """Entrada de games.json con el esquema de enrich.py."""
    return {
        "name": name, "genres": list(genres or []), "categories": [], "metacritic": None,
        "tags": dict(tags or {}), "reviews_positive": pos, "reviews_total": total,
        "hltb_main": hltb_main, "hltb_extra": None, "missing": [],
    }


def make_backlog(*rows) -> list[dict]:
    """make_backlog(("1", "Uno", "pendiente"), ...) → filas de backlog.csv."""
    return [{"appid": a, "name": n, "estado": e, "prioridad": "", "notas": ""} for a, n, e in rows]


def write_raw(path, rows):
    """Escribe un steam_library.csv sintético con la cabecera de export.py."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RAW_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({"source": "propio", **r})
