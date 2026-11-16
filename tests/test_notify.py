from notify import message_for, send


def test_no_message_when_the_shared_library_worked():
    assert message_for({"shared_ok": True}) is None
    assert message_for({"shared_ok": True, "token_source": "refresh"}) is None


def test_message_about_the_manual_token_when_there_is_no_refresh_token():
    text = message_for({"shared_ok": False, "shared_error": "HTTP Error 401: Unauthorized"})
    assert "HTTP Error 401" in text and "ajaxgetasyncconfig" in text and "STEAM_WEBAPI_TOKEN" in text
    assert "STEAM_REFRESH_TOKEN" in text  # y que se puede dejar de renovar a diario


def test_message_about_the_refresh_token_says_it_lasts_months():
    text = message_for({"shared_ok": False, "token_source": "",
                        "refresh_error": "RuntimeError: HTTP Error 401: Unauthorized"})
    assert "STEAM_REFRESH_TOKEN" in text and "steamRefresh_steam" in text
    assert "HTTP Error 401" in text and "meses" in text
    assert "ajaxgetasyncconfig" not in text  # ese es el token viejo, no el que toca renovar


def test_message_when_the_token_is_fine_and_steam_failed_today():
    text = message_for({"shared_ok": False, "token_source": "refresh", "refresh_error": "",
                        "shared_error": "HTTP Error 500: Internal Server Error"})
    assert "HTTP Error 500" in text and "no hace falta" in text
    assert "STEAM_REFRESH_TOKEN" not in text  # el token sigue bueno: no hay nada que renovar


def test_message_when_the_refresh_token_died_but_the_manual_one_salvó_el_día():
    text = message_for({"shared_ok": True, "token_source": "webapi",
                        "refresh_error": "RuntimeError: HTTP Error 401: Unauthorized"})
    assert "STEAM_REFRESH_TOKEN" in text and "steamRefresh_steam" in text


def test_send_posts_to_bot_api_and_survives_errors():
    seen = []
    assert send("hola", "123:ABC", "42", post=lambda url, data: seen.append((url, data)))
    url, data = seen[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage" and b"chat_id=42" in data

    def boom(url, data):
        raise OSError("sin red")
    assert send("hola", "123:ABC", "42", post=boom) is False
