from datetime import date

import pytest

from conftest import write_raw
from library import Playtime, load_playtime, read_backlog, set_estado, write_backlog


def rows():
    return [
        {"appid": "10", "name": "B", "estado": "pendiente", "prioridad": "", "notas": ""},
        {"appid": "5", "name": "A", "estado": "jugando", "prioridad": "2", "notas": "ok"},
    ]


def test_backlog_roundtrip_preserves_order(tmp_path):
    p = tmp_path / "backlog.csv"
    write_backlog(rows(), p)
    assert read_backlog(p) == rows()


def test_set_estado_changes_only_target(tmp_path):
    p = tmp_path / "backlog.csv"
    write_backlog(rows(), p)
    assert set_estado("5", "terminado", p) is True
    result = read_backlog(p)
    assert result[0] == rows()[0]
    assert result[1] == {**rows()[1], "estado": "terminado"}


def test_set_estado_rejects_unknown_estado(tmp_path):
    p = tmp_path / "backlog.csv"
    write_backlog(rows(), p)
    with pytest.raises(ValueError):
        set_estado("5", "ganado", p)
    assert read_backlog(p) == rows()


def test_set_estado_unknown_appid_returns_false(tmp_path):
    p = tmp_path / "backlog.csv"
    write_backlog(rows(), p)
    assert set_estado("999", "terminado", p) is False
    assert read_backlog(p) == rows()


def test_load_playtime_keeps_last_known_hours(tmp_path):
    raw, cache = tmp_path / "raw.csv", tmp_path / "cache.json"
    write_raw(raw, [
        {"appid": "1", "name": "Uno", "playtime_hours": "3.0", "last_played": "2026-10-01"},
        {"appid": "2", "name": "Dos", "playtime_hours": "5.0", "last_played": ""},
    ])
    first = load_playtime(raw, cache)
    assert first == {"1": Playtime(3.0, date(2026, 10, 1)), "2": Playtime(5.0, None)}

    # Token familiar caducado: el export ya no trae el juego compartido "2".
    write_raw(raw, [
        {"appid": "1", "name": "Uno", "playtime_hours": "4.0", "last_played": "2026-11-01"},
    ])
    second = load_playtime(raw, cache)
    assert second == {"1": Playtime(4.0, date(2026, 11, 1)), "2": Playtime(5.0, None)}


def test_load_playtime_empty_last_played_is_none(tmp_path):
    raw, cache = tmp_path / "raw.csv", tmp_path / "cache.json"
    write_raw(raw, [{"appid": "7", "name": "X", "playtime_hours": "", "last_played": ""}])
    assert load_playtime(raw, cache) == {"7": Playtime(0.0, None)}
