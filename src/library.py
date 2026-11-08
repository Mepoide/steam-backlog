"""Lectura y escritura del backlog y de las horas jugadas."""
import csv
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config import BACKLOG_CSV, PLAYTIME_CACHE, RAW_CSV

FIELDS = ["appid", "name", "estado", "prioridad", "notas"]
ESTADOS = frozenset({"pendiente", "jugando", "terminado", "abandonado", "descartado"})


@dataclass
class Playtime:
    hours: float
    last_played: date | None


def _atomic_write_text(path: Path, write) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            write(f)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def atomic_write_json(path, obj) -> None:
    _atomic_write_text(path, lambda f: json.dump(obj, f, ensure_ascii=False, indent=1))


def read_backlog(path=BACKLOG_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: row.get(k, "") or "" for k in FIELDS} for row in csv.DictReader(f)]


def write_backlog(rows, path=BACKLOG_CSV) -> None:
    def write(f):
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    _atomic_write_text(path, write)


def set_estado(appid: str, estado: str, path=BACKLOG_CSV) -> bool:
    if estado not in ESTADOS:
        raise ValueError(f"Estado no válido: {estado}")
    rows = read_backlog(path)
    for row in rows:
        if row["appid"] == str(appid):
            row["estado"] = estado
            write_backlog(rows, path)
            return True
    return False


STEAM_EPOCH = date(2003, 9, 12)  # antes de Steam no hay fechas reales (a veces llega 1970-01-01)


def _parse_date(s: str) -> date | None:
    d = date.fromisoformat(s) if s else None
    return d if d is not None and d >= STEAM_EPOCH else None


def read_library(raw_csv=RAW_CSV) -> dict[str, dict]:
    """Datos del export por appid, solo lectura: origen, horas y fechas (sin SteamIDs)."""
    raw_csv = Path(raw_csv)
    if not raw_csv.exists():
        return {}
    out = {}
    with open(raw_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["appid"]] = {
                "name": row.get("name") or "",
                "source": row.get("source") or "",
                "hours": float(row.get("playtime_hours") or 0),
                "last_played": _parse_date(row.get("last_played") or ""),
                "acquired": _parse_date(row.get("acquired") or ""),
            }
    return out


def load_playtime(raw_csv=RAW_CSV, cache_path=PLAYTIME_CACHE) -> dict[str, Playtime]:
    """Horas por appid: el CSV actual manda; la caché conserva los juegos que
    hayan desaparecido del export (p. ej. si caducó el token familiar)."""
    cache_path = Path(cache_path)
    merged: dict[str, dict] = {}
    if cache_path.exists():
        merged = json.loads(cache_path.read_text(encoding="utf-8"))
    with open(raw_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            merged[row["appid"]] = {
                "hours": float(row.get("playtime_hours") or 0),
                "last_played": row.get("last_played") or "",
            }
    atomic_write_json(cache_path, merged)
    return {k: Playtime(v["hours"], _parse_date(v["last_played"])) for k, v in merged.items()}
