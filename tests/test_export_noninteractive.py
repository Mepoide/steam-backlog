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


COOKIE_JWT = "cabecera.eyJzdWIiOiAiNzY1NiIsICJleHAiOiAxODAwMDAwMDAwfQ.firma"
COOKIE_NAVEGADOR = f"76561198000000000%7C%7C{COOKIE_JWT}"   # como la enseña DevTools
COOKIE_DECODIFICADA = f"76561198000000000||{COOKIE_JWT}"    # por si se pega ya decodificada


def test_refresh_cookie_se_manda_como_la_manda_el_navegador(monkeypatch):
    """Steam espera la cookie entera y codificada; el «||» se vuelve a codificar."""
    export = _export_module(monkeypatch)
    assert export.refresh_cookie(COOKIE_DECODIFICADA) == COOKIE_NAVEGADOR
    assert export.refresh_cookie(f"  {COOKIE_NAVEGADOR} \n") == COOKIE_NAVEGADOR
    assert export.refresh_cookie("") == ""


def test_refresh_token_expiry_reads_the_date_from_the_cookie(monkeypatch):
    """Sirve para saber cuándo habrá que renovarla (ops/steam_refresh_token.py)."""
    export = _export_module(monkeypatch)
    assert export.refresh_token_expiry(COOKIE_NAVEGADOR) == "2027-01-15"
    assert export.refresh_token_expiry(COOKIE_DECODIFICADA) == "2027-01-15"
    assert export.refresh_token_expiry("esto-no-es-una-cookie") == ""
    assert export.refresh_token_expiry("") == ""


def test_webapi_token_sale_de_ajaxgetasyncconfig(monkeypatch):
    """La cookie se canjea por el mismo webapi_token que se copia hoy a mano."""
    export = _export_module(monkeypatch)
    seen = []

    def fake_fetch(cookie):
        seen.append(cookie)
        return '{"data": {"webapi_token": "token-del-dia"}, "success": 1}'

    assert export.webapi_token(COOKIE_DECODIFICADA, fetch=fake_fetch) == "token-del-dia"
    assert seen == [COOKIE_NAVEGADOR]  # entera y codificada, no solo el JWT


def test_webapi_token_falla_claro_si_steam_no_lo_da(monkeypatch):
    export = _export_module(monkeypatch)
    with pytest.raises(RuntimeError, match="STEAM_REFRESH_TOKEN"):
        export.webapi_token(COOKIE_NAVEGADOR, fetch=lambda cookie: '{"success": 2}')


def test_shared_library_token_prefers_the_refresh_token(monkeypatch):
    export = _export_module(monkeypatch)
    token, source, error = export.shared_library_token(
        COOKIE_NAVEGADOR, "viejo-de-24h", exchange=lambda cookie: "token-del-dia")
    assert (token, source, error) == ("token-del-dia", "refresh", "")


def test_shared_library_token_falls_back_to_the_manual_token(monkeypatch):
    export = _export_module(monkeypatch)

    def boom(cookie):
        raise RuntimeError("AccessDenied")

    token, source, error = export.shared_library_token(COOKIE_NAVEGADOR, "manual", exchange=boom)
    assert (token, source) == ("manual", "webapi")
    assert "AccessDenied" in error


def test_shared_library_token_without_refresh_token_uses_the_manual_one(monkeypatch):
    export = _export_module(monkeypatch)
    assert export.shared_library_token("", "manual") == ("manual", "webapi", "")
    assert export.shared_library_token("", "") == ("", "", "")


def test_shared_library_token_reports_when_nothing_is_left(monkeypatch):
    export = _export_module(monkeypatch)

    def boom(cookie):
        raise RuntimeError("AccessDenied")

    token, source, error = export.shared_library_token(COOKIE_NAVEGADOR, "", exchange=boom)
    assert (token, source) == ("", "")
    assert "AccessDenied" in error


def _run_main(export, monkeypatch, tmp_path, env, shared=None, exchange=None):
    """main() sin red: RAW_DIR en tmp_path y las tres llamadas a Steam inyectadas."""
    monkeypatch.setattr(export, "RAW_DIR", tmp_path)
    monkeypatch.setattr(export, "owned_games", lambda key, steamid: {
        10: {"appid": 10, "name": "Propio", "source": "propio", "acquired": "", "owners": steamid}})
    monkeypatch.setattr(export, "shared_games", shared or (lambda token, steamid: {
        20: {"appid": 20, "name": "Familiar", "source": "compartido", "acquired": "2024-01-01",
             "owners": "8"}}))
    if exchange is not None:
        monkeypatch.setattr(export, "webapi_token", exchange)
    for name in ("STEAM_API_KEY", "STEAM_ID", "STEAM_REFRESH_TOKEN", "STEAM_WEBAPI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    export.main()
    return json.loads((tmp_path / "export_status.json").read_text(encoding="utf-8"))


def test_export_renews_the_shared_token_with_the_refresh_token(monkeypatch, tmp_path):
    export = _export_module(monkeypatch)
    used, canjeado = [], []
    status = _run_main(
        export, monkeypatch, tmp_path,
        {"STEAM_API_KEY": "k", "STEAM_ID": "7656", "STEAM_REFRESH_TOKEN": COOKIE_DECODIFICADA},
        shared=lambda token, steamid: used.append(token) or {},
        exchange=lambda cookie: canjeado.append(cookie) or "token-del-dia")
    assert used == ["token-del-dia"]          # a la familiar va el token del día
    assert canjeado == [COOKIE_NAVEGADOR]     # y al canje, la cookie entera y codificada
    assert status["token_source"] == "refresh" and status["refresh_error"] == ""


def test_export_records_the_expired_refresh_token_for_the_telegram_notice(monkeypatch, tmp_path):
    export = _export_module(monkeypatch)

    def boom(cookie):
        raise RuntimeError("Steam no devolvió webapi_token")

    status = _run_main(export, monkeypatch, tmp_path,
                       {"STEAM_API_KEY": "k", "STEAM_ID": "7656",
                        "STEAM_REFRESH_TOKEN": COOKIE_NAVEGADOR}, exchange=boom)
    assert status["shared_ok"] is False and status["token_source"] == ""
    assert "webapi_token" in status["refresh_error"]


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
