import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXPORT = Path(__file__).resolve().parent.parent / "src" / "export.py"


def run_export(tmp_path, extra_env=None):
    # Copia aislada: ROOT apunta a tmp_path, así no se lee el .env real ni se toca la red.
    (tmp_path / "src").mkdir()
    shutil.copy(EXPORT, tmp_path / "src" / "export.py")
    env = {"PATH": os.environ["PATH"], **(extra_env or {})}
    return subprocess.run([sys.executable, str(tmp_path / "src" / "export.py")], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)


def test_export_noninteractive_missing_key_exits(tmp_path):
    r = run_export(tmp_path)
    assert r.returncode != 0
    assert "Falta STEAM_API_KEY en .env" in r.stderr


def test_export_noninteractive_missing_steam_id_exits(tmp_path):
    r = run_export(tmp_path, {"STEAM_API_KEY": "x"})
    assert r.returncode != 0
    assert "Falta STEAM_ID en .env" in r.stderr


def _export_module(monkeypatch):
    import importlib
    monkeypatch.setitem(sys.modules, "dotenv", None)  # que no cargue el .env real
    monkeypatch.syspath_prepend(str(EXPORT.parent))
    return importlib.import_module("export")


def test_refresh_token_accepts_the_cookie_tal_cual(monkeypatch):
    """La cookie steamRefresh_steam vale «steamid||token»: se copia entera sin recortar."""
    export = _export_module(monkeypatch)
    assert export.clean_refresh_token("76561198000000000||eyJ.a.b") == "eyJ.a.b"
    assert export.clean_refresh_token("  eyJ.a.b \n") == "eyJ.a.b"
    assert export.clean_refresh_token("") == ""


def test_access_token_asks_steam_with_the_refresh_token(monkeypatch):
    export = _export_module(monkeypatch)
    seen = []

    def fake_post(path, params):
        seen.append((path, params))
        return {"response": {"access_token": "nuevo-de-24h"}}

    assert export.access_token("eyJ.a.b", "7656", post=fake_post) == "nuevo-de-24h"
    path, params = seen[0]
    assert path == "IAuthenticationService/GenerateAccessTokenForApp/v1"
    assert params == {"refresh_token": "eyJ.a.b", "steamid": "7656", "renewal_type": 0}


def test_access_token_fails_loud_when_steam_returns_nothing(monkeypatch):
    export = _export_module(monkeypatch)
    with pytest.raises(RuntimeError, match="refresh token"):
        export.access_token("caducado", "7656", post=lambda path, params: {"response": {}})


def test_shared_library_token_prefers_the_refresh_token(monkeypatch):
    export = _export_module(monkeypatch)
    token, source, error = export.shared_library_token(
        "eyJ.a.b", "viejo-de-24h", "7656", exchange=lambda r, s: "nuevo-de-24h")
    assert (token, source, error) == ("nuevo-de-24h", "refresh", "")


def test_shared_library_token_falls_back_to_the_manual_token(monkeypatch):
    export = _export_module(monkeypatch)

    def boom(refresh, steamid):
        raise RuntimeError("401 Unauthorized")

    token, source, error = export.shared_library_token("caducado", "manual", "7656", exchange=boom)
    assert (token, source) == ("manual", "webapi")
    assert "401 Unauthorized" in error


def test_shared_library_token_without_refresh_token_uses_the_manual_one(monkeypatch):
    export = _export_module(monkeypatch)
    assert export.shared_library_token("", "manual", "7656") == ("manual", "webapi", "")
    assert export.shared_library_token("", "", "7656") == ("", "", "")


def test_shared_library_token_reports_when_nothing_is_left(monkeypatch):
    export = _export_module(monkeypatch)

    def boom(refresh, steamid):
        raise RuntimeError("401 Unauthorized")

    token, source, error = export.shared_library_token("caducado", "", "7656", exchange=boom)
    assert (token, source) == ("", "")
    assert "401 Unauthorized" in error


def _run_main(export, monkeypatch, tmp_path, env, shared=None, exchange=None):
    """main() sin red: RAW_DIR en tmp_path y las tres llamadas a Steam inyectadas."""
    monkeypatch.setattr(export, "RAW_DIR", tmp_path)
    monkeypatch.setattr(export, "owned_games", lambda key, steamid: {
        10: {"appid": 10, "name": "Propio", "source": "propio", "acquired": "", "owners": steamid}})
    monkeypatch.setattr(export, "shared_games", shared or (lambda token, steamid: {
        20: {"appid": 20, "name": "Familiar", "source": "compartido", "acquired": "2024-01-01",
             "owners": "8"}}))
    if exchange is not None:
        monkeypatch.setattr(export, "access_token", exchange)
    for name in ("STEAM_API_KEY", "STEAM_ID", "STEAM_REFRESH_TOKEN", "STEAM_WEBAPI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    export.main()
    return json.loads((tmp_path / "export_status.json").read_text(encoding="utf-8"))


def test_export_renews_the_shared_token_with_the_refresh_token(monkeypatch, tmp_path):
    export = _export_module(monkeypatch)
    used = []
    status = _run_main(
        export, monkeypatch, tmp_path,
        {"STEAM_API_KEY": "k", "STEAM_ID": "7656", "STEAM_REFRESH_TOKEN": "7656||eyJ.a.b"},
        shared=lambda token, steamid: used.append(token) or {},
        exchange=lambda refresh, steamid: "nuevo-de-24h")
    assert used == ["nuevo-de-24h"]  # el token del día, no el refresh token entero
    assert status["token_source"] == "refresh" and status["refresh_error"] == ""


def test_export_records_the_expired_refresh_token_for_the_telegram_notice(monkeypatch, tmp_path):
    export = _export_module(monkeypatch)

    def boom(refresh, steamid):
        raise RuntimeError("HTTP Error 401: Unauthorized")

    status = _run_main(export, monkeypatch, tmp_path,
                       {"STEAM_API_KEY": "k", "STEAM_ID": "7656", "STEAM_REFRESH_TOKEN": "caducado"},
                       exchange=boom)
    assert status["shared_ok"] is False and status["token_source"] == ""
    assert "401" in status["refresh_error"]


def test_export_without_any_token_keeps_saying_so(monkeypatch, tmp_path):
    export = _export_module(monkeypatch)
    status = _run_main(export, monkeypatch, tmp_path, {"STEAM_API_KEY": "k", "STEAM_ID": "7656"})
    assert status["shared_ok"] is False and status["token_source"] == ""
    assert status["refresh_error"] == "" and status["shared_error"] == "sin STEAM_WEBAPI_TOKEN"


def test_keep_previous_restores_shared_games_and_dates(monkeypatch, tmp_path):
    export = _export_module(monkeypatch)
    lib = {"10": {"appid": 10, "name": "Propio", "source": "propio", "acquired": "", "owners": "7"}}
    previous = [
        {"appid": "10", "name": "Propio", "source": "propio", "acquired": "2022-11-09", "owners": "7;8"},
        {"appid": "20", "name": "Familiar", "source": "compartido", "playtime_hours": "3.5",
         "acquired": "2024-01-01", "owners": "8"},
    ]
    assert export.keep_previous(lib, previous) == 1
    assert lib["10"]["acquired"] == "2022-11-09" and lib["10"]["owners"] == "7"  # no pisa lo nuevo
    assert lib["20"]["source"] == "compartido" and lib["20"]["acquired"] == "2024-01-01"
    assert export.read_previous(tmp_path / "no.csv") == []
