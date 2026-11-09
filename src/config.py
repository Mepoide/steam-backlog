"""Rutas del proyecto y carga de secretos desde .env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_CSV = DATA_DIR / "raw" / "steam_library.csv"
ENRICHED_DIR = DATA_DIR / "enriched"
GAMES_JSON = ENRICHED_DIR / "games.json"
PROFILE_JSON = ENRICHED_DIR / "profile.json"
SCORES_JSON = ENRICHED_DIR / "scores.json"
PLAYTIME_CACHE = ENRICHED_DIR / "playtime_cache.json"
LLM_USAGE = DATA_DIR / "llm_usage.jsonl"
IMAGES_DIR = DATA_DIR / "images"
PREFS_JSON = DATA_DIR / "prefs.json"  # preferencias del chat que sobreviven a reinicios
BACKLOG_CSV = ROOT / "backlog.csv"


def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Falta {name} en .env")
    return value
