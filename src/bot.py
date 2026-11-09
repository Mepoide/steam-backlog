#!/usr/bin/env python3
"""Bot de Telegram del backlog (long polling: no abre puertos).

Solo responde al chat de TELEGRAM_ALLOWED_CHAT_ID. Los mensajes de otros chats
se registran en el log con su chat_id (útil para configurarlo la primera vez).

    python src/bot.py
"""
import asyncio
import logging
import os

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
                      LinkPreviewOptions, Update)
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import commands
import taste
from config import load_env, require_env

log = logging.getLogger("bot")
ERROR_TEXT = "Algo ha fallado; mira el log del bot."
MAX_MESSAGE = 4096
CAPTION_MAX = 1024  # pie de foto: si el texto cabe, va con la imagen en un solo mensaje
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
SETUP_CHAT_ID = 0  # ningún chat de Telegram tiene id 0: modo configuración, no responde a nadie
HISTORY = 3  # turnos que recuerda para entender «¿y esos?» o «márcalos»
MENU = (("top", "Tus filas: para ti, sesión corta, joyas"),
        ("recomienda", "Filtra por duración y géneros"),
        ("buscar", "Busca un juego en el backlog"),
        ("juego", "Ficha: horas jugadas, fechas, duración"),
        ("parecidos", "Lo que más se parece a un juego"),
        ("recientes", "Lo último que llegó a tu biblioteca"),
        ("marcar", "Cambia el estado de un juego"),
        ("repasar", "Clasifica los empezados (o /repasar <saga>)"),
        ("coste", "Gasto de IA de este mes"),
        ("ayuda", "Qué puedo hacer"))


def _markup(reply: commands.Reply):
    if not reply.buttons:
        return None
    buttons = [InlineKeyboardButton(label, callback_data=data) for label, data in reply.buttons]
    n = max(reply.columns, 1)
    return InlineKeyboardMarkup([buttons[i:i + n] for i in range(0, len(buttons), n)])


def remember(chat_data: dict, text: str, reply: commands.Reply) -> None:
    """Guarda el turno (texto y juegos mostrados) para dar contexto al siguiente mensaje."""
    history = chat_data.setdefault("history", [])
    history.append({"user": text, "shown": reply.shown})
    del history[:-HISTORY]


async def _compute(fn) -> commands.Reply:
    try:
        return await asyncio.to_thread(fn)
    except Exception:
        log.exception("Error atendiendo un mensaje")
        return commands.Reply(ERROR_TEXT)


async def deliver(bot, chat_id: int, reply: commands.Reply) -> None:
    """Envía la respuesta con sus imágenes.

    Una imagen y texto corto → foto con el texto de pie (y sus botones). Varias → álbum
    con «1. Nombre» de pie y después el texto. Si Telegram rechaza las imágenes, va
    solo el texto: las imágenes adornan, nunca impiden responder."""
    text, markup = reply.text[:MAX_MESSAGE], _markup(reply)
    photos = reply.photos[:commands.MAX_PHOTOS]
    try:
        if len(photos) == 1 and len(reply.text) <= CAPTION_MAX:
            await bot.send_photo(chat_id, photos[0][0], caption=reply.text, reply_markup=markup)
            return
        if len(photos) == 1:
            await bot.send_photo(chat_id, photos[0][0], caption=photos[0][1])
        elif photos:
            await bot.send_media_group(chat_id, [InputMediaPhoto(url, caption=cap) for url, cap in photos])
    except TelegramError as e:
        log.warning("No se pudieron enviar las imágenes (%s); va solo el texto", e)
    await bot.send_message(chat_id, text, reply_markup=markup, link_preview_options=NO_PREVIEW)


async def replace(bot, message, reply: commands.Reply) -> None:
    """Sustituye el mensaje de un botón (repaso, confirmación) por la nueva respuesta.

    Foto → foto se edita en sitio (el repaso cambia de juego sin llenar el chat); si
    cambia el tipo (foto ↔ texto) o la edición falla, se borra y se envía de nuevo."""
    markup = _markup(reply)
    photo = reply.photos[0][0] if reply.photos and len(reply.text) <= CAPTION_MAX else None
    try:
        if photo and message.photo:
            await message.edit_media(InputMediaPhoto(photo, caption=reply.text), reply_markup=markup)
            return
        if not photo and not message.photo:
            await message.edit_text(reply.text[:MAX_MESSAGE], reply_markup=markup,
                                    link_preview_options=NO_PREVIEW)
            return
    except BadRequest as e:
        if "not modified" in str(e).lower():  # doble toque en el mismo botón
            return
        log.warning("No se pudo editar el mensaje (%s); lo envío de nuevo", e)
    try:
        await message.delete()
    except TelegramError:
        pass
    await deliver(bot, message.chat.id, reply)


async def _send(update: Update, fn, context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
    text = update.effective_message.text or ""
    log.info("Mensaje: %s", text or "(sin texto)")
    reply = await _compute(fn)
    if context is not None:
        remember(context.chat_data, text, reply)
    await deliver(update.get_bot(), update.effective_chat.id, reply)


def _args(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args or [])


async def on_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, commands.cmd_ayuda)


async def on_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, lambda: commands.cmd_top(commands.load_store()), context)


async def on_recomienda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_recomienda(commands.load_store(), args), context)


async def on_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_buscar(commands.load_store(), args), context)


async def on_marcar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_marcar(commands.load_store(), args), context)


async def on_juego(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_juego(commands.load_store(), args), context)


async def on_recientes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _args(context).strip()
    limit = min(max(int(raw), 1), 30) if raw.isdigit() else 10
    await _send(update, lambda: commands.cmd_recientes(commands.load_store(), limit), context)


async def on_parecidos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = _args(context)
    await _send(update, lambda: commands.cmd_parecidos(commands.load_store(), args), context)


async def on_repasar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state, query = context.chat_data, _args(context)
    await _send(update, lambda: commands.start_review(commands.load_store(), state, query), context)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text or ""
    # Los turnos anteriores dan contexto a «¿y de estrategia?», «¿esos los tengo?» o «márcalos».
    history, state = list(context.chat_data.get("history") or []), context.chat_data
    await _send(update, lambda: commands.free_text(commands.load_store(), text, history=history,
                                                   state=state), context)


async def on_coste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, commands.cmd_coste)


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
        log.info("Botón: %s", data)
        reply = await _compute(lambda: callback_reply(data, context.chat_data))
        if reply.shown and not reply.buttons:
            # Fin de un repaso o marcado confirmado: «¿y sus estados?» debe saber de qué juegos hablamos.
            remember(context.chat_data, "(pulsó un botón)", reply)
        await replace(context.bot, query.message, reply)
    return handler


def callback_reply(data: str, chat_data: dict) -> commands.Reply:
    if data.startswith(("mark:", "marks:")):
        pairs = commands.parse_marks(data)
        if not pairs:
            return commands.Reply("Botón no válido.")
        return commands.confirm_marks(pairs, refresh=taste.refresh)
    if data.startswith("rev:"):
        return commands.review_step(data, chat_data, refresh=taste.refresh)
    return commands.Reply("Cancelado.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, NetworkError):
        # Cortes de Telegram (Bad Gateway, timeouts): la librería reintenta sola.
        log.warning("Red de Telegram: %s (reintentando)", err)
    else:
        log.error("Error no controlado", exc_info=err)


async def _post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands([BotCommand(n, d) for n, d in MENU])
    except Exception:
        log.warning("No se pudo publicar el menú de comandos", exc_info=True)


def build_application(token: str, allowed_chat_id: int) -> Application:
    app = Application.builder().token(token).post_init(_post_init).build()
    allowed = filters.Chat(chat_id=allowed_chat_id)
    for names, fn in ((["start", "ayuda"], on_ayuda), (["top"], on_top),
                      (["recomienda"], on_recomienda), (["buscar"], on_buscar),
                      (["juego"], on_juego), (["parecidos"], on_parecidos),
                      (["recientes"], on_recientes), (["marcar"], on_marcar),
                      (["repasar"], on_repasar), (["coste"], on_coste)):
        app.add_handler(CommandHandler(names, fn, filters=allowed))
    app.add_handler(MessageHandler(allowed & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(allowed & (filters.COMMAND | ~filters.TEXT), on_unknown))
    app.add_handler(CallbackQueryHandler(_on_callback(allowed_chat_id)))
    app.add_handler(MessageHandler(~allowed, on_unauthorized), group=1)
    app.add_error_handler(on_error)
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
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # el de anthropic: una línea por llamada
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
