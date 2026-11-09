from notify import message_for, send


def test_message_only_when_shared_library_failed():
    assert message_for({"shared_ok": True}) is None
    text = message_for({"shared_ok": False, "shared_error": "HTTP Error 401: Unauthorized"})
    assert "HTTP Error 401" in text and "ajaxgetasyncconfig" in text and "STEAM_WEBAPI_TOKEN" in text


def test_send_posts_to_bot_api_and_survives_errors():
    seen = []
    assert send("hola", "123:ABC", "42", post=lambda url, data: seen.append((url, data)))
    url, data = seen[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage" and b"chat_id=42" in data

    def boom(url, data):
        raise OSError("sin red")
    assert send("hola", "123:ABC", "42", post=boom) is False
