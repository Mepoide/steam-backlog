from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from bot import build_application


def test_build_application_registers_commands():
    app = build_application("123:ABC", 42)
    cmds = {c for h in app.handlers[0] if isinstance(h, CommandHandler) for c in h.commands}
    assert cmds == {"start", "ayuda", "top", "recomienda", "buscar", "juego", "parecidos",
                    "recientes", "marcar", "repasar", "coste"}


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


def test_menu_lists_every_public_command():
    from bot import MENU
    app = build_application("123:ABC", 42)
    cmds = {c for h in app.handlers[0] if isinstance(h, CommandHandler) for c in h.commands}
    assert {name for name, _ in MENU} == cmds - {"start"}


def test_network_errors_are_one_line_warnings(caplog):
    import asyncio
    from types import SimpleNamespace
    from telegram.error import NetworkError
    from bot import on_error
    app = build_application("123:ABC", 42)
    assert app.error_handlers
    with caplog.at_level("WARNING", logger="bot"):
        asyncio.run(on_error(None, SimpleNamespace(error=NetworkError("Bad Gateway"))))
    [r] = caplog.records
    assert r.levelname == "WARNING" and "Bad Gateway" in r.getMessage() and r.exc_info is None


def test_other_errors_keep_traceback(caplog):
    import asyncio
    from types import SimpleNamespace
    from bot import on_error
    with caplog.at_level("ERROR", logger="bot"):
        asyncio.run(on_error(None, SimpleNamespace(error=ValueError("x"))))
    assert caplog.records[0].exc_info is not None


def test_remember_keeps_last_turns():
    from bot import HISTORY, remember
    from commands import Reply
    data = {}
    for i in range(HISTORY + 2):
        remember(data, f"m{i}", Reply("x", shown=[(str(i), f"G{i}")]))
    assert [h["user"] for h in data["history"]] == [f"m{i}" for i in range(2, HISTORY + 2)]


def test_markup_columns():
    from bot import _markup
    from commands import Reply
    m = _markup(Reply("x", [(str(i), str(i)) for i in range(5)], columns=3))
    assert [len(r) for r in m.inline_keyboard] == [3, 2]
    assert _markup(Reply("x")) is None


def test_callback_reply_routes(monkeypatch):
    import commands
    from bot import callback_reply
    seen = []
    monkeypatch.setattr(commands, "confirm_marks", lambda pairs, refresh=None: seen.append(pairs) or
                        commands.Reply("ok"))
    monkeypatch.setattr(commands, "review_step", lambda data, state, refresh=None: commands.Reply(data))
    assert callback_reply("marks:6=t", {}).text == "ok" and seen == [[("6", "terminado")]]
    assert callback_reply("marks:", {}).text == "Botón no válido."
    assert callback_reply("rev:6:t", {}).text == "rev:6:t"
    assert callback_reply("cancel", {}).text == "Cancelado."


class FakeBot:
    def __init__(self, fail_photos=False):
        self.sent, self.fail_photos = [], fail_photos

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        if self.fail_photos:
            from telegram.error import BadRequest
            raise BadRequest("wrong file identifier")
        self.sent.append(("photo", photo, caption, reply_markup is not None))

    async def send_media_group(self, chat_id, media):
        self.sent.append(("album", [m.media for m in media], [m.caption for m in media]))

    async def send_message(self, chat_id, text, reply_markup=None, link_preview_options=None):
        self.sent.append(("text", text, reply_markup is not None))


class FakeMessage:
    def __init__(self, photo=False, edit_error=None):
        from types import SimpleNamespace
        self.photo = ("p",) if photo else ()
        self.chat = SimpleNamespace(id=42)
        self.calls, self.edit_error = [], edit_error

    async def edit_media(self, media, reply_markup=None):
        if self.edit_error:
            raise self.edit_error
        self.calls.append(("edit_media", media.media, media.caption))

    async def edit_text(self, text, reply_markup=None, link_preview_options=None):
        if self.edit_error:
            raise self.edit_error
        self.calls.append(("edit_text", text))

    async def delete(self):
        self.calls.append(("delete",))


def test_deliver_single_photo_with_caption_album_and_fallback():
    import asyncio
    from bot import deliver
    from commands import Reply
    b = FakeBot()
    asyncio.run(deliver(b, 42, Reply("ficha", [("Sí", "x")], photos=[("u1", "Hades")])))
    assert b.sent == [("photo", "u1", "ficha", True)]
    b = FakeBot()
    asyncio.run(deliver(b, 42, Reply("lista", photos=[("u1", "1. A"), ("u2", "2. B")])))
    assert b.sent == [("album", ["u1", "u2"], ["1. A", "2. B"]), ("text", "lista", False)]
    b = FakeBot()
    asyncio.run(deliver(b, 42, Reply("x" * 2000, photos=[("u1", "Hades")])))
    assert b.sent[0] == ("photo", "u1", "Hades", False) and b.sent[1][0] == "text"
    b = FakeBot(fail_photos=True)
    asyncio.run(deliver(b, 42, Reply("ficha", photos=[("u1", "Hades")])))
    assert b.sent == [("text", "ficha", False)]


def test_replace_edits_in_place_or_resends():
    import asyncio
    from telegram.error import BadRequest
    from bot import replace
    from commands import Reply
    m, b = FakeMessage(photo=True), FakeBot()
    asyncio.run(replace(b, m, Reply("siguiente", photos=[("u2", "B")])))
    assert m.calls == [("edit_media", "u2", "siguiente")] and b.sent == []
    m, b = FakeMessage(photo=True), FakeBot()  # fin del repaso: de foto a texto
    asyncio.run(replace(b, m, Reply("Repaso terminado")))
    assert m.calls == [("delete",)] and b.sent == [("text", "Repaso terminado", False)]
    m, b = FakeMessage(), FakeBot()
    asyncio.run(replace(b, m, Reply("✓ marcado")))
    assert m.calls == [("edit_text", "✓ marcado")]
    m, b = FakeMessage(edit_error=BadRequest("Message is not modified")), FakeBot()
    asyncio.run(replace(b, m, Reply("igual")))
    assert m.calls == [] and b.sent == []
