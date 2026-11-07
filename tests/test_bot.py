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
