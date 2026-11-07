import csv

RAW_FIELDS = ["appid", "name", "source", "playtime_hours", "playtime_2weeks_hours",
              "last_played", "acquired", "owners", "excluded_reason"]


def write_raw(path, rows):
    """Escribe un steam_library.csv sintético con la cabecera de export.py."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RAW_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({"source": "propio", **r})
