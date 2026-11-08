#!/usr/bin/env python3
"""Pone assets/bot-icon.jpg como foto de perfil del bot (Bot API setMyProfilePhoto).

    .venv/bin/python ops/set_bot_photo.py

Se ejecuta una vez (o al cambiar el icono). No hace falta parar el bot: no usa polling.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from telegram import Bot, InputProfilePhotoStatic  # noqa: E402

from config import load_env, require_env  # noqa: E402

ICON = ROOT / "assets" / "bot-icon.jpg"


async def main() -> None:
    load_env()
    async with Bot(require_env("TELEGRAM_BOT_TOKEN")) as bot:
        with open(ICON, "rb") as f:
            await bot.set_my_profile_photo(InputProfilePhotoStatic(photo=f))
    print(f"✓ foto de perfil actualizada con {ICON.name}")


if __name__ == "__main__":
    asyncio.run(main())
