from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from bot import build_application


def test_build_application_registers_commands():
    app = build_application("123:ABC", 42)
    cmds = {c for h in app.handlers[0] if isinstance(h, CommandHandler) for c in h.commands}
    assert cmds == {"start", "ayuda", "top", "recomienda", "buscar", "marcar"}


def test_build_application_has_text_callback_and_unauthorized_handlers():
    app = build_application("123:ABC", 42)
    kinds = {type(h) for h in app.handlers[0]}
    assert MessageHandler in kinds and CallbackQueryHandler in kinds
    assert any(isinstance(h, MessageHandler) for h in app.handlers[1])


def test_allowed_chat_id_parsing():
    import pytest
    from bot import SETUP_CHAT_ID, parse_allowed_chat_id
    assert parse_allowed_chat_id("123456") == 123456
    assert parse_allowed_chat_id(" -100123 ") == -100123
    assert parse_allowed_chat_id("") == SETUP_CHAT_ID
    with pytest.raises(SystemExit):
        parse_allowed_chat_id("mi chat")


def _update(app, chat_id, text=None, sticker=False):
    from datetime import datetime, timezone
    from telegram import Chat, Message, MessageEntity, Sticker, Update, User
    # Sin red: CommandHandler necesita el username del bot, que normalmente da getMe.
    app.bot._bot_user = User(id=123, first_name="Bot", is_bot=True, username="testbot")
    entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))] \
        if text and text.startswith("/") else None
    extra = {"sticker": Sticker("f", "u", 1, 1, False, False, "regular")} if sticker else {}
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="private"),
                  text=text, entities=entities, **extra)
    msg.set_bot(app.bot)
    upd = Update(update_id=1, message=msg)
    upd.set_bot(app.bot)
    return upd


def _matching_callbacks(app, update):
    return [h.callback.__name__ for h in app.handlers[0] if h.check_update(update)]


def test_unknown_command_and_non_text_get_help():
    app = build_application("123:ABC", 42)
    assert _matching_callbacks(app, _update(app, 42, "/top"))[0] == "on_top"
    assert _matching_callbacks(app, _update(app, 42, "/help"))[0] == "on_unknown"
    assert _matching_callbacks(app, _update(app, 42, sticker=True))[0] == "on_unknown"
    assert _matching_callbacks(app, _update(app, 42, "algo tranquilo"))[0] == "on_text"


def test_other_chats_match_nothing_in_main_group():
    app = build_application("123:ABC", 42)
    for upd in (_update(app, 99, "/top"), _update(app, 99, "hola"), _update(app, 99, "/help")):
        assert _matching_callbacks(app, upd) == []


def test_unknown_reply_text():
    from bot import unknown_reply
    assert unknown_reply().text.startswith("No te he entendido.")
    assert "/recomienda" in unknown_reply().text
