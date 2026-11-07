from types import SimpleNamespace

import pytest

import llm as real_llm
from commands import (Store, cmd_ayuda, cmd_buscar, cmd_marcar, cmd_recomienda, cmd_top,
                      confirm_mark, find_games, free_text, parse_recomienda_args, render)
from conftest import make_backlog, make_game
from library import read_backlog, write_backlog
from recommender import Filters, Recommendation


def sc(score, anchor=None, name=None, shared=()):
    return {"score": score, "sim": 0.0, "quality": 0.0, "anchor_appid": anchor,
            "anchor_name": name, "shared_tags": list(shared)}


def store():
    games = {
        "5": make_game("Hades", tags={"Roguelike": 10}, hltb_main=23.6, pos=97, total=1000),
        "6": make_game("Hades II", tags={"Roguelike": 10}, hltb_main=40, pos=95, total=1000),
        "7": make_game("Stardew Valley", genres=["Simulación"], hltb_main=53, pos=98, total=1000),
        "8": make_game("Celeste", tags={"Plataformas": 10}, hltb_main=8.2),
    }
    scores = {"5": sc(0.9, "1", "Dead Cells", ["Roguelike"]), "6": sc(0.8), "7": sc(0.7), "8": sc(0.6)}
    backlog = make_backlog(("5", "Hades", "pendiente"), ("6", "Hades II", "pendiente"),
                           ("7", "Stardew Valley", "pendiente"), ("8", "Celeste", "pendiente"),
                           ("9", "Portal 2", "terminado"))
    return Store(games=games, scores=scores, backlog=backlog)


def test_render_format():
    recs = [Recommendation("1145360", "Hades", 0.9, "porque jugaste X", 23.6, 97),
            Recommendation("2", "Raro", 0.1, "sin datos de reseñas", None, None),
            Recommendation("3", "Corto", 0.1, "x", 2.25, 80)]
    assert render("Para ti", recs) == (
        "Para ti\n"
        "1. Hades — 24 h · 97 % 👍\n   porque jugaste X\n   https://store.steampowered.com/app/1145360\n"
        "2. Raro — ? h · ? % 👍\n   sin datos de reseñas\n   https://store.steampowered.com/app/2\n"
        "3. Corto — 2.2 h · 80 % 👍\n   x\n   https://store.steampowered.com/app/3")
    assert render(None, recs[:1]).startswith("1. Hades")


def test_ayuda_lists_commands():
    text = cmd_ayuda().text
    for c in ("/top", "/recomienda", "/buscar", "/marcar"):
        assert c in text


def test_top_renders_rows():
    text = cmd_top(store()).text
    assert text.startswith("Para ti\n1. Hades")
    assert "Portal 2" not in text


def test_top_without_data():
    assert "taste.py" in cmd_top(Store(games={}, scores={}, backlog=[])).text


def test_recomienda_parses_hours_and_tags():
    assert parse_recomienda_args("1,5h estrategia") == Filters(max_hours=1.5, include_tags=["estrategia"], limit=5)
    assert parse_recomienda_args("10 h roguelike, mundo abierto") == Filters(
        max_hours=10, include_tags=["roguelike", "mundo abierto"], limit=5)
    assert parse_recomienda_args("90min") == Filters(max_hours=1.5, limit=5)
    assert parse_recomienda_args("") == Filters(limit=5)


def test_recomienda_filters():
    text = cmd_recomienda(store(), "10h").text
    assert "Celeste" in text and "Hades" not in text


def test_recomienda_zero_results_message():
    assert cmd_recomienda(store(), "1h carreras").text == (
        "Nada encaja con esos filtros. Prueba con más horas o sin género.")


def test_find_games_exact_substring_and_fuzzy():
    backlog = store().backlog
    assert [r["appid"] for r in find_games(backlog, "hades")] == ["5"]
    assert [r["appid"] for r in find_games(backlog, "HADES I")] == ["6"]
    assert [r["appid"] for r in find_games(backlog, "stardew")] == ["7"]
    assert [r["appid"] for r in find_games(backlog, "celest")] == ["8"]
    assert [r["appid"] for r in find_games(backlog, "selestre")] == ["8"]
    assert find_games(backlog, "zzzz") == []


def test_buscar_shows_estado():
    text = cmd_buscar(store(), "portal").text
    assert "Portal 2" in text and "terminado" in text
    assert cmd_buscar(store(), "zzzz").text == "No encuentro «zzzz» en el backlog."
    assert cmd_buscar(store(), "").text.startswith("Uso: /buscar")


def test_marcar_single_match_asks_confirmation():
    s = store()
    before = [dict(r) for r in s.backlog]
    reply = cmd_marcar(s, "stardew terminado")
    assert reply.text == "¿Marcar «Stardew Valley» como terminado?"
    assert reply.buttons == [("Sí", "mark:7:terminado"), ("Cancelar", "cancel")]
    assert s.backlog == before


def test_marcar_ambiguous_offers_choices():
    reply = cmd_marcar(store(), "hade jugando")
    assert reply.text == "¿Cuál quieres marcar como jugando?"
    assert reply.buttons == [("Hades", "mark:5:jugando"), ("Hades II", "mark:6:jugando"),
                             ("Cancelar", "cancel")]


def test_marcar_no_match_and_bad_estado():
    assert cmd_marcar(store(), "zzzz terminado").text == "No encuentro «zzzz» en el backlog."
    bad = cmd_marcar(store(), "hades ganado")
    assert bad.text.startswith("Estado no válido: «ganado».") and bad.buttons == []
    assert "terminado" in bad.text
    assert cmd_marcar(store(), "").text.startswith("Uso: /marcar")
    assert cmd_marcar(store(), "terminado").text.startswith("Uso: /marcar")


def test_marcar_accepts_estado_with_caps():
    assert cmd_marcar(store(), "celeste Terminado").buttons[0] == ("Sí", "mark:8:terminado")


@pytest.fixture
def backlog_file(tmp_path):
    p = tmp_path / "backlog.csv"
    write_backlog(store().backlog, p)
    return p


def test_confirm_mark_writes_and_syncs(backlog_file):
    calls = []
    reply = confirm_mark("7", "terminado", backlog_path=backlog_file,
                         sync=lambda paths, msg: calls.append((paths, msg)) or True)
    assert reply.text == "✓ «Stardew Valley» marcado como terminado."
    assert calls == [(["backlog.csv"], "backlog: Stardew Valley → terminado")]
    assert {r["appid"]: r["estado"] for r in read_backlog(backlog_file)}["7"] == "terminado"


def test_confirm_mark_reports_push_failure(backlog_file):
    reply = confirm_mark("7", "terminado", backlog_path=backlog_file, sync=lambda p, m: False)
    assert reply.text.endswith("(no se pudo subir a GitHub, queda en local)")


def test_confirm_mark_missing_game(backlog_file):
    reply = confirm_mark("999", "terminado", backlog_path=backlog_file, sync=lambda p, m: True)
    assert reply.text == "Ese juego ya no está en el backlog."


def fake_llm(filters=None, intro="Aquí tienes.", parse_error=False, intro_error=False):
    def parse_request(text, known_tags):
        if parse_error:
            raise real_llm.LLMError("caído")
        return filters or Filters()

    def write_intro(text, recs):
        if intro_error:
            raise real_llm.LLMError("caído")
        return intro

    return SimpleNamespace(parse_request=parse_request, write_intro=write_intro,
                           LLMError=real_llm.LLMError)


def test_free_text_uses_llm_filters():
    reply = free_text(store(), "algo corto", llm=fake_llm(Filters(max_hours=10)))
    assert reply.text.startswith("Aquí tienes.\n\n1. Celeste")
    assert "Hades" not in reply.text


def test_free_text_llm_error_falls_back_to_para_ti():
    reply = free_text(store(), "x", llm=fake_llm(parse_error=True))
    assert reply.text.startswith(
        "No he podido usar la IA ahora mismo; aquí van tus recomendaciones de siempre:\n\n1. Hades")


def test_free_text_intro_error_still_lists_games():
    reply = free_text(store(), "x", llm=fake_llm(intro_error=True))
    assert reply.text.startswith("1. Hades")


def test_free_text_zero_results_falls_back():
    reply = free_text(store(), "x", llm=fake_llm(Filters(include_tags=["carreras"])))
    assert reply.text.startswith(
        "No he encontrado nada exacto; esto es lo que más te encaja en general:\n\n1. Hades")
