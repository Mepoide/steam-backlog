#!/usr/bin/env python3
"""
Enriquece la biblioteca exportada con género, duración estimada y valoración.

Lee data/raw/steam_library.json y escribirá data/enriched/steam_library.json.

TODO: sin implementar. Fuentes candidatas: appdetails de la Store API
(género, descripción), HowLongToBeat (duración) y reseñas de Steam
(valoración). Decidir fuentes y límites de peticiones antes de implementar.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT / "data" / "raw" / "steam_library.json"
ENRICHED_DIR = ROOT / "data" / "enriched"


def enrich() -> None:
    """Lee data/raw/, añade género/duración/valoración y escribe data/enriched/."""
    raise NotImplementedError("enrich.py es un stub; ver TODO en la cabecera")


if __name__ == "__main__":
    enrich()
