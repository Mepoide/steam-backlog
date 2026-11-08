from types import SimpleNamespace

import pytest

import llm as real_llm
from datetime import date

from commands import (NO_DATA, Store, cmd_ayuda, cmd_buscar, cmd_coste, cmd_juego, cmd_marcar,
                      cmd_recientes, cmd_recomienda, cmd_top, confirm_mark, find_games, free_text,
                      parse_recomienda_args, render)
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
    library = {
        "5": lib("Hades", "compartido", 0.0, None, date(2021, 7, 1)),
        "6": lib("Hades II", "propio", 71.8, date(2026, 11, 8), date(2026, 7, 22)),
        "7": lib("Stardew Valley", "propio", 1.0, date(2024, 1, 2), date(2023, 12, 25)),
        "8": lib("Celeste", "compartido", 0.0, None, None),
        "9": lib("Portal 2", "propio", 12.0, date(2020, 5, 1), date(2019, 3, 3)),
    }
    return Store(games=games, scores=scores, backlog=backlog, library=library)


def lib(name, source, hours, last, acquired):
    return {"name": name, "source": source, "hours": hours, "last_played": last, "acquired": acquired}


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
    for c in ("/top", "/recomienda", "/buscar", "/juego", "/recientes", "/marcar", "/coste"):
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


def fake_llm(filters=None, intro="Aquí tienes.", parse_error=False, intro_error=False, request=None,
             seen=None):
    def parse_request(text, known_tags, previous=None):
        if seen is not None:
            seen.append(previous)
        if parse_error:
            raise real_llm.LLMError("caído")
        return request or real_llm.Request(filters=filters or Filters())

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


def test_free_text_logs_llm_errors(caplog):
    with caplog.at_level("WARNING"):
        free_text(store(), "x", llm=fake_llm(parse_error=True))
        free_text(store(), "x", llm=fake_llm(intro_error=True))
    msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(msgs) == 2 and all("caído" in m for m in msgs)


def test_free_text_budget_exhausted_says_so():
    def parse_request(text, known_tags, previous=None):
        raise real_llm.BudgetExceeded("presupuesto mensual de IA agotado (1.00 de 1.00 $)")
    fake = SimpleNamespace(parse_request=parse_request, write_intro=None, LLMError=real_llm.LLMError)
    reply = free_text(store(), "x", llm=fake)
    assert reply.text.startswith("Este mes ya se ha gastado el presupuesto de IA (/coste); "
                                 "aquí van tus recomendaciones de siempre:\n\n1. Hades")


def test_free_text_without_data_does_not_call_llm():
    def boom(*a):
        raise AssertionError("no debería llamar al LLM")
    fake = SimpleNamespace(parse_request=boom, write_intro=boom, LLMError=real_llm.LLMError)
    assert free_text(Store(games={}, scores={}, backlog=[]), "x", llm=fake).text == NO_DATA


def test_free_text_logs_request_and_result(caplog):
    with caplog.at_level("INFO", logger="commands"):
        free_text(store(), "algo corto", llm=fake_llm(Filters(max_hours=10)))
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "algo corto" in msg and "max_hours=10" in msg and "Celeste" in msg


def test_coste_reports_month_spend():
    import usage
    usage.record(usage.Usage(model=real_llm.MODEL, kind="parse", input_tokens=30_000, output_tokens=1_000))
    text = cmd_coste(budget=1.0).text
    assert text.startswith("IA este mes: 1 llamadas · 30.0K tokens de entrada · 1.0K de salida")
    assert "0.0035 $ de 1.00 $ (0 %)" in text


def test_coste_without_budget_and_unpriced():
    import usage
    usage.record(usage.Usage(model="claude-otro", kind="parse", input_tokens=10))
    text = cmd_coste(budget=0).text
    assert "sin tope" in text and "1 llamadas sin precio conocido" in text



def test_juego_shows_playtime_dates_and_store_data():
    text = cmd_juego(store(), "hades").text
    first, second = text.split("\n\n")
    assert first.startswith("Hades — pendiente\nSin jugar · biblioteca familiar, disponible desde 2021-07-01")
    assert "Duración: 24 h historia principal (HowLongToBeat)" in first
    assert "Reseñas: 10 % 👍 de 1000" in first  # fixture: 97 de 1000 and "Tags: Roguelike" in first
    assert first.endswith("https://store.steampowered.com/app/5")
    assert second.startswith("Hades II — pendiente\n71.8 h jugadas (última vez 2026-11-08) · "
                             "comprado el 2026-07-22")


def test_juego_errors():
    assert cmd_juego(store(), "").text.startswith("Uso: /juego")
    assert cmd_juego(store(), "zzzz").text == "No encuentro «zzzz» en tu biblioteca."


def test_recientes_sorted_by_acquired():
    text = cmd_recientes(store(), 3).text
    assert text.splitlines() == [
        "Últimos juegos añadidos a tu biblioteca:",
        "1. Hades II — 2026-07-22 · propio · 71.8 h · pendiente",
        "2. Stardew Valley — 2023-12-25 · propio · 1.0 h · pendiente",
        "3. Hades — 2021-07-01 · familiar · sin jugar · pendiente",
    ]
    assert cmd_recientes(Store(games={}, scores={}, backlog=[]), 5).text.startswith("No tengo las fechas")


def test_free_text_routes_game_and_recent_without_intro():
    r = free_text(store(), "cuánto he jugado al Hades",
                  llm=fake_llm(request=real_llm.Request(kind="game", game="Hades II"), intro_error=True))
    assert r.text.startswith("Hades II — pendiente\n71.8 h jugadas")
    r = free_text(store(), "qué he comprado hace poco",
                  llm=fake_llm(request=real_llm.Request(kind="recent", limit=1)))
    assert r.text.startswith("Últimos juegos añadidos a tu biblioteca:\n1. Hades II")


def test_free_text_passes_previous_message():
    seen = []
    free_text(store(), "¿y de rol?", llm=fake_llm(seen=seen), previous="algo corto")
    assert seen == ["algo corto"]



def test_juego_suggests_marcar_for_started_pending_games():
    text = cmd_juego(store(), "hades ii").text
    assert "Sigue en «pendiente» y no te lo recomiendo: /marcar Hades II jugando" in text
    assert "/marcar" not in cmd_juego(store(), "celeste").text
