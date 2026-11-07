#!/usr/bin/env python3
"""Bot de Telegram del backlog (long polling: no abre puertos).

Solo responde al chat de TELEGRAM_ALLOWED_CHAT_ID. Los mensajes de otros chats
se registran en el log con su chat_id (útil para configurarlo la primera vez).

    python src/bot.py
"""
import asyncio
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import commands
from config import load_env, require_env

log = logging.getLogger("bot")
ERROR_TEXT = "Algo ha fallado; mira el log del bot."
MAX_MESSAGE = 4096
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
SETUP_CHAT_ID = 0  # ningún chat de Telegram tiene id 0: modo configuración, no responde a nadie


def _markup(reply: commands.Reply):
    if not reply.buttons:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)]
                                 for label, data in reply.buttons])


async def _compute(fn) -> commands.Reply:
    try:
        return await asyncio.to_thread(fn)
    except Exception:
        log.exception("Error atendiendo un mensaje")
        return commands.Reply(ERROR_TEXT)


async def _send(update: Update, fn) -> None:
    reply = await _compute(fn)
    await update.effective_message.reply_text(reply.text[:MAX_MESSAGE], reply_markup=_markup(reply),
                                              link_preview_options=NO_PREVIEW)


def _args(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args or [])


async def on_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, commands.cmd_ayuda)


async def on_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, lambda: commands.cmd_top(commands.load_store()))


async def on_recomienda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_recomienda(commands.load_store(), args))


async def on_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_buscar(commands.load_store(), args))


async def on_marcar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_marcar(commands.load_store(), args))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    await _send(update, lambda: commands.free_text(commands.load_store(), text))


def unknown_reply() -> commands.Reply:
    return commands.Reply("No te he entendido.\n\n" + commands.cmd_ayuda().text)


async def on_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, unknown_reply)


async def on_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    log.warning("Chat no autorizado: %s", chat.id if chat else "?")


def _on_callback(allowed_chat_id: int):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query.message is None or query.message.chat.id != allowed_chat_id:
            log.warning("Callback de chat no autorizado: %s",
                        query.message.chat.id if query.message else "?")
            await query.answer()
            return
        await query.answer()
        data = query.data or ""
        if data.startswith("mark:"):
            _, appid, estado = data.split(":", 2)
            reply = await _compute(lambda: commands.confirm_mark(appid, estado))
        else:
            reply = commands.Reply("Cancelado.")
        await query.edit_message_text(reply.text[:MAX_MESSAGE], link_preview_options=NO_PREVIEW)
    return handler


def build_application(token: str, allowed_chat_id: int) -> Application:
    app = Application.builder().token(token).build()
    allowed = filters.Chat(chat_id=allowed_chat_id)
    for names, fn in ((["start", "ayuda"], on_ayuda), (["top"], on_top),
                      (["recomienda"], on_recomienda), (["buscar"], on_buscar),
                      (["marcar"], on_marcar)):
        app.add_handler(CommandHandler(names, fn, filters=allowed))
    app.add_handler(MessageHandler(allowed & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(allowed & (filters.COMMAND | ~filters.TEXT), on_unknown))
    app.add_handler(CallbackQueryHandler(_on_callback(allowed_chat_id)))
    app.add_handler(MessageHandler(~allowed, on_unauthorized), group=1)
    return app


def parse_allowed_chat_id(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        return SETUP_CHAT_ID
    try:
        return int(raw)
    except ValueError:
        raise SystemExit("TELEGRAM_ALLOWED_CHAT_ID debe ser un número (tu chat_id)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx registra cada URL de la API de Telegram, que incluye el token del bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_env()
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = parse_allowed_chat_id(os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", ""))
    if chat_id == SETUP_CHAT_ID:
        log.warning("Sin TELEGRAM_ALLOWED_CHAT_ID: no respondo a nadie. Escríbele al bot, copia el "
                    "chat_id que aparecerá abajo como «Chat no autorizado» en .env y reinícialo.")
    else:
        log.info("Bot arrancado; solo respondo al chat %s", chat_id)
    build_application(token, chat_id).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
