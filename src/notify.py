#!/usr/bin/env python3
"""Avisos del job diario por Telegram (al mismo chat que atiende el bot).

Todos van de la biblioteca familiar, y distinguen de quién es la culpa:

- caducó STEAM_REFRESH_TOKEN (dura meses): hay que renovarlo en el .env del Mac;
- no hay refresh token y caducó STEAM_WEBAPI_TOKEN (dura ~24 h): lo de siempre;
- el token está bien y Steam falló hoy: no hay nada que hacer.

Sin token de Telegram o sin chat, no hace nada.

    python src/notify.py
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from config import DATA_DIR, load_env

log = logging.getLogger("notify")
STATUS = DATA_DIR / "raw" / "export_status.json"
DEGRADED = ("Sigo con los juegos y fechas del último export bueno, pero las horas de los juegos "
            "familiares no se actualizan.")
REFRESH_HELP = ("Para renovarla: con la sesión de Steam iniciada en el navegador, abre "
                "login.steampowered.com/jwt/refresh y, en DevTools → Application → Cookies, "
                "elige el origen login.steampowered.com y copia el valor ENTERO de la cookie "
                "«steamRefresh_steam» (con el 7656…%7C%7C de delante) a STEAM_REFRESH_TOKEN en "
                "el .env del Mac; luego ejecuta scripts/daily.sh. La nueva dura meses.")

TOKEN_HELP = ("⚠ Hoy no he podido leer la biblioteca familiar ({error}).\n"
              + DEGRADED + "\n\n"
              "Para renovarlo: con la sesión de Steam iniciada en el navegador, abre "
              "https://store.steampowered.com/pointssummary/ajaxgetasyncconfig, copia el valor de "
              "«webapi_token» a STEAM_WEBAPI_TOKEN en el .env del Mac y ejecuta scripts/daily.sh.\n\n"
              "Para no tener que hacerlo cada día, pon un STEAM_REFRESH_TOKEN (ver README).")
REFRESH_EXPIRED = ("⚠ Caducó el token de la biblioteca familiar ({error}).\n"
                   + DEGRADED + "\n\n" + REFRESH_HELP)
REFRESH_EXPIRED_BUT_OK = ("⚠ Caducó STEAM_REFRESH_TOKEN ({error}), pero hoy he leído la biblioteca "
                          "familiar con el STEAM_WEBAPI_TOKEN manual, que dura ~24 h.\n\n"
                          + REFRESH_HELP)
STEAM_FAILED = ("⚠ Hoy no he podido leer la biblioteca familiar ({error}).\n"
                + DEGRADED + "\n\n"
                "El token está bien, así que no hace falta que toques nada: lo reintento mañana. "
                "Si se repite varios días, Steam habrá cambiado algo.")


def message_for(status: dict) -> str | None:
    """El aviso que toca según el estado del export, o None si todo fue bien."""
    refresh_error = status.get("refresh_error") or ""
    if status.get("shared_ok", True):
        # El refresh token ya no vale, pero el manual ha salvado el día: conviene saberlo.
        return REFRESH_EXPIRED_BUT_OK.format(error=refresh_error) if refresh_error else None
    if refresh_error:
        return REFRESH_EXPIRED.format(error=refresh_error)
    if status.get("token_source") == "refresh":
        return STEAM_FAILED.format(error=status.get("shared_error") or "error desconocido")
    return TOKEN_HELP.format(error=status.get("shared_error") or "error desconocido")


def send(text: str, token: str, chat_id: str, post=None) -> bool:
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                   "link_preview_options": json.dumps({"is_disabled": True})}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        if post is not None:
            post(url, data)
        else:
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30).close()
        return True
    except OSError as e:  # la URL lleva el token: no se registra
        log.warning("No se pudo enviar el aviso por Telegram: %s", type(e).__name__)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not (token and chat_id and STATUS.exists()):
        return
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    if status.get("date") != datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        return  # el export de hoy no llegó a escribirlo: no repetir el aviso de otro día
    text = message_for(status)
    if text and send(text, token, chat_id):
        print("✓ aviso enviado por Telegram")


if __name__ == "__main__":
    main()
