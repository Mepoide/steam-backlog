#!/usr/bin/env python3
"""
Añade a backlog.csv los juegos nuevos de data/raw/steam_library.csv con
estado "pendiente". No modifica ni elimina filas existentes.

backlog.csv es el único fichero con datos que se versiona: solo lleva
appid, name, estado, prioridad y notas (sin SteamIDs ni horas).
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = ROOT / "data" / "raw" / "steam_library.csv"
BACKLOG = ROOT / "backlog.csv"

FIELDS = ["appid", "name", "estado", "prioridad", "notas"]
ESTADOS = {"pendiente", "jugando", "terminado", "abandonado", "descartado"}


def main():
    if not RAW_CSV.exists():
        sys.exit(f"No existe {RAW_CSV}. Ejecuta antes src/export.py.")

    existing = {}
    if BACKLOG.exists():
        with open(BACKLOG, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["appid"]] = row

    new_rows = []
    with open(RAW_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            appid = row["appid"]
            if appid not in existing:
                new_rows.append({
                    "appid": appid,
                    "name": row.get("name", ""),
                    "estado": "pendiente",
                    "prioridad": "",
                    "notas": "",
                })

    if not new_rows:
        print(f"Sin novedades: backlog.csv ya tiene los {len(existing)} juegos.")
        return

    with open(BACKLOG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(existing.values())
        w.writerows(sorted(new_rows, key=lambda r: r["name"].lower()))

    print(f"✓ {len(new_rows)} juegos nuevos añadidos como 'pendiente' "
          f"({len(existing) + len(new_rows)} en total).")


if __name__ == "__main__":
    main()
