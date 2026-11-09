import json
from types import SimpleNamespace

import pytest

import llm as real_llm
from datetime import date

from commands import (NO_DATA, Store, cmd_ayuda, cmd_buscar, cmd_coste, cmd_juego, cmd_marcar,
                      cmd_mostrados, cmd_parecidos, cmd_recientes, cmd_recomienda, cmd_repasar,
                      cmd_top, confirm_mark, confirm_marks, find_games, finish_review, free_text,
                      parse_marks, parse_recomienda_args, plan_marks, render, review_step,
                      start_review, started_pending)
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
    def parse_request(text, known_tags, history=None):
        if seen is not None:
            seen.append(history)
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
    def parse_request(text, known_tags, history=None):
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
    assert first.startswith("Hades — ⏳ pendiente\nSin jugar · biblioteca familiar, disponible desde 2021-07-01")
    assert "Duración: 24 h historia principal (HowLongToBeat)" in first
    assert "Reseñas: 10 % 👍 de 1000" in first  # fixture: 97 de 1000 and "Tags: Roguelike" in first
    assert first.endswith("https://store.steampowered.com/app/5")
    assert second.startswith("Hades II — ⏳ pendiente\n71.8 h jugadas (última vez 2026-11-08) · "
                             "comprado el 2026-07-22")


def test_juego_errors():
    assert cmd_juego(store(), "").text.startswith("Uso: /juego")
    assert cmd_juego(store(), "zzzz").text == "No encuentro «zzzz» en tu biblioteca."


def test_recientes_sorted_by_acquired():
    text = cmd_recientes(store(), 3).text
    assert text.splitlines() == [
        "Últimos juegos añadidos a tu biblioteca:",
        "1. Hades II — 2026-07-22 · propio · 71.8 h · ⏳ pendiente",
        "2. Stardew Valley — 2023-12-25 · propio · 1.0 h · ⏳ pendiente",
        "3. Hades — 2021-07-01 · familiar · sin jugar · ⏳ pendiente",
    ]
    assert cmd_recientes(Store(games={}, scores={}, backlog=[]), 5).text.startswith("No tengo las fechas")


def test_free_text_routes_game_and_recent_without_intro():
    r = free_text(store(), "cuánto he jugado al Hades",
                  llm=fake_llm(request=real_llm.Request(kind="game", games=["Hades II"]), intro_error=True))
    assert r.text.startswith("Hades II — ⏳ pendiente\n71.8 h jugadas")
    r = free_text(store(), "qué he comprado hace poco",
                  llm=fake_llm(request=real_llm.Request(kind="recent", limit=1)))
    assert r.text.startswith("Últimos juegos añadidos a tu biblioteca:\n1. Hades II")


def test_free_text_passes_history_with_shown_names():
    seen = []
    history = [{"user": "/juego hades", "shown": [("5", "Hades"), ("6", "Hades II")]},
               {"user": "algo corto", "shown": []}]
    free_text(store(), "¿y de rol?", llm=fake_llm(seen=seen), history=history)
    assert seen == [[real_llm.Turn("/juego hades", ["Hades", "Hades II"]), real_llm.Turn("algo corto")]]



def test_juego_suggests_marcar_for_started_pending_games():
    text = cmd_juego(store(), "hades ii").text
    assert "Sigue en «pendiente» y no te lo recomiendo: /marcar Hades II jugando" in text
    assert "/marcar" not in cmd_juego(store(), "celeste").text


def test_find_games_tolerates_word_variants():
    backlog = make_backlog(("1", "Sherlock Holmes Chapter One", "pendiente"),
                           ("2", "Sherlock Holmes: The Devil's Daughter", "pendiente"))
    assert [r["appid"] for r in find_games(backlog, "chapters one")] == ["1"]
    assert [r["appid"] for r in find_games(backlog, "devils daughter")] == ["2"]


def test_commands_report_shown_games():
    assert [a for a, _ in cmd_juego(store(), "hades").shown] == ["5", "6"]
    assert cmd_recomienda(store(), "").shown[0] == ("5", "Hades")
    assert cmd_recientes(store(), 1).shown == [("6", "Hades II")]


def test_mostrados_answers_where_games_come_from():
    text = cmd_mostrados(store(), [("5", "Hades"), ("7", "Stardew Valley")]).text
    assert text.splitlines() == [
        "Sí, todos están en tu biblioteca:",
        "1. Hades — biblioteca familiar, disponible desde 2021-07-01 · dura 24 h · sin jugar · ⏳ pendiente",
        "2. Stardew Valley — comprado el 2023-12-25 · dura 53 h · 1.0 h · ⏳ pendiente",
    ]
    assert cmd_mostrados(store(), []).text.startswith("Aún no te he enseñado")


def test_plan_marks_prefers_shown_games_and_builds_one_button():
    shown = [("6", "Hades II")]
    reply = plan_marks(store(), [("Hades", "terminado"), ("celeste", "abandonado"),
                                 ("Portal 2", "terminado"), ("zzzz", "jugando")], shown)
    assert reply.text.splitlines() == [
        "¿Hago estos cambios?",
        "• Hades II: ⏳ pendiente → ✅ terminado",  # «Hades» se refería al mostrado
        "• Celeste: ⏳ pendiente → 💤 abandonado",
        "No encuentro «zzzz» en el backlog.",
    ]
    assert reply.buttons == [("Sí", "marks:6=t,8=a"), ("Cancelar", "cancel")]
    assert all(len(d.encode()) <= 64 for _, d in reply.buttons)
    assert plan_marks(store(), [("Portal 2", "terminado")]).text.startswith("Ya estaban así")


def test_parse_marks_new_and_old_formats():
    assert parse_marks("marks:6=t,8=a,9=x") == [("6", "terminado"), ("8", "abandonado")]
    assert parse_marks("mark:7:terminado") == [("7", "terminado")]


def test_confirm_marks_writes_all_syncs_once_and_refreshes(backlog_file):
    calls, refreshed = [], []
    reply = confirm_marks([("6", "terminado"), ("8", "abandonado"), ("999", "jugando")],
                          backlog_path=backlog_file,
                          sync=lambda paths, msg: calls.append(msg) or True,
                          refresh=lambda: refreshed.append(1))
    assert reply.text == "✓ Marcados:\n• Hades II → ✅ terminado\n• Celeste → 💤 abandonado"
    assert calls == ["backlog: Hades II → terminado, Celeste → abandonado"] and refreshed == [1]
    estados = {r["appid"]: r["estado"] for r in read_backlog(backlog_file)}
    assert estados["6"] == "terminado" and estados["8"] == "abandonado"


def test_refresh_failure_does_not_break_marking(backlog_file):
    def boom():
        raise RuntimeError("sin datos")
    reply = confirm_mark("7", "terminado", backlog_path=backlog_file, sync=lambda p, m: True, refresh=boom)
    assert reply.text == "✓ «Stardew Valley» marcado como terminado."


def test_parecidos():
    s = store()
    s.games["8"]["tags"] = {"Roguelike": 10, "Plataformas": 3}
    reply = cmd_parecidos(s, "hades ii")
    assert reply.text.startswith("Lo más parecido a Hades II que no has empezado:\n1. Hades")
    assert "parecido a Hades II (comparten Roguelike)" in reply.text
    assert "1. Hades II" not in reply.text
    assert cmd_parecidos(s, "").text.startswith("Uso: /parecidos")
    assert cmd_parecidos(s, "zzzz").text == "No encuentro «zzzz» en tu biblioteca."


def started_store():
    s = store()
    s.library["7"]["hours"] = 5.0  # Stardew: empezado, jugado el 2024-01-02
    s.library["8"].update(hours=3.0, last_played=date(2025, 6, 1))  # Celeste, más reciente
    return s


def test_started_pending_order_and_repasar():
    s = started_store()
    assert [r["appid"] for r in started_pending(s)] == ["6", "8", "7"]
    reply = cmd_repasar(s, skip={"6"})
    assert reply.text.startswith("Repaso de juegos empezados que siguen en pendiente (quedan 2)\n\nCeleste")
    assert "/marcar" not in reply.text
    assert reply.buttons[0] == ("Terminado", "rev:8:t") and reply.buttons[-1] == ("Parar", "rev:stop")
    assert reply.columns == 3
    assert cmd_repasar(s, skip={"6", "7", "8"}) is None


def test_review_flow_marks_locally_and_syncs_once_at_the_end(backlog_file):
    s, state, calls = started_store(), {}, []
    sync = lambda paths, msg: calls.append(msg) or True

    def load():
        s.backlog = read_backlog(backlog_file)
        return s

    assert start_review(s, state).buttons[0] == ("Terminado", "rev:6:t")
    nxt = review_step("rev:6:t", state, backlog_path=backlog_file, sync=sync, load=load)
    assert "(quedan 2, llevas 1)" in nxt.text and calls == []
    nxt = review_step("rev:8:s", state, backlog_path=backlog_file, sync=sync, load=load)
    assert "Stardew Valley" in nxt.text
    done = review_step("rev:7:a", state, backlog_path=backlog_file, sync=sync, load=load)
    assert done.text == ("Repaso terminado: 2 juegos actualizados\n"
                         "• Hades II → ✅ terminado\n• Stardew Valley → 💤 abandonado")
    assert done.shown == [("6", "Hades II"), ("7", "Stardew Valley")]
    assert calls == ["backlog: repaso (2 juegos)"]
    estados = {r["appid"]: r["estado"] for r in read_backlog(backlog_file)}
    assert (estados["6"], estados["7"], estados["8"]) == ("terminado", "abandonado", "pendiente")


def test_review_stop_and_nothing_to_review():
    state = {"review_done": [], "review_active": True}
    assert review_step("rev:stop", state, sync=lambda p, m: True).text.startswith(
        "Repaso terminado; no has cambiado nada")
    s = store()
    assert start_review(Store(games={}, scores={}, backlog=s.backlog[-1:], library=s.library),
                        {}).text.startswith("No tienes juegos empezados")


def test_free_text_mark_uses_last_shown_games():
    history = [{"user": "/juego hades", "shown": [("6", "Hades II")]}]
    req = real_llm.Request(kind="mark", marks=[("Hades", "terminado")])
    reply = free_text(store(), "me lo he pasado", llm=fake_llm(request=req), history=history)
    assert reply.buttons[0] == ("Sí", "marks:6=t")


def test_free_text_last_results():
    history = [{"user": "rol", "shown": [("5", "Hades")]}, {"user": "gracias", "shown": []}]
    reply = free_text(store(), "¿esos los tengo?", llm=fake_llm(request=real_llm.Request(kind="last")),
                      history=history)
    assert reply.text.startswith("Sí, todos están en tu biblioteca:\n1. Hades")


def test_free_text_like_game():
    s = store()
    s.games["8"]["tags"] = {"Roguelike": 10}
    req = real_llm.Request(filters=Filters(limit=1), game="Hades II")
    reply = free_text(s, "algo como Hades II", llm=fake_llm(request=req))
    assert "parecido a Hades II" in reply.text
    reply = free_text(s, "algo como Zelda", llm=fake_llm(request=real_llm.Request(game="Zelda")))
    assert reply.text.startswith("No encuentro «Zelda» en tu biblioteca; ")


def test_juegos_several_names_one_card_each():
    reply = free_text(store(), "awakened y celeste", llm=fake_llm(
        request=real_llm.Request(kind="game", games=["Hades II", "celeste", "zzzz"])))
    cards = reply.text.split("\n\n")
    assert cards[0].startswith("Hades II — ⏳ pendiente") and cards[1].startswith("Celeste — ⏳ pendiente")
    assert cards[2] == "No encuentro «zzzz» en tu biblioteca."
    assert [a for a, _ in reply.shown] == ["6", "8"]


def test_review_by_name_includes_unstarted_and_keeps_filter(backlog_file):
    s, state = started_store(), {}
    first = start_review(s, state, "hades")
    assert first.text.startswith("Repaso de «hades» (quedan 2)\n\nHades II")
    assert state["review_query"] == "hades"

    def load():
        s.backlog = read_backlog(backlog_file)
        return s
    nxt = review_step("rev:6:t", state, backlog_path=backlog_file, sync=lambda p, m: True, load=load)
    assert nxt.text.startswith("Repaso de «hades» (quedan 1, llevas 1)\n\nHades —")
    assert start_review(s, {}, "zzzz").text == "No encuentro juegos que casen con «zzzz»."


def test_free_text_review_uses_chat_state():
    state = {}
    reply = free_text(started_store(), "repasemos los de hades", state=state,
                      llm=fake_llm(request=real_llm.Request(kind="review", game="hades")))
    assert reply.buttons[0] == ("Terminado", "rev:6:t") and state["review_query"] == "hades"


def test_review_double_tap_and_buttons_after_finish(backlog_file):
    s, state, calls = started_store(), {}, []
    sync = lambda paths, msg: calls.append(msg) or True

    def load():
        s.backlog = read_backlog(backlog_file)
        return s
    start_review(s, state, "hades")
    review_step("rev:6:t", state, backlog_path=backlog_file, sync=sync, load=load)
    again = review_step("rev:6:a", state, backlog_path=backlog_file, sync=sync, load=load)  # doble toque
    assert "llevas 1" in again.text
    done = review_step("rev:5:t", state, backlog_path=backlog_file, sync=sync, load=load)
    assert done.text.startswith("Repaso terminado: 2 juegos actualizados")
    late = review_step("rev:5:t", state, backlog_path=backlog_file, sync=sync, load=load)
    assert late.text == "Este repaso ya terminó. Empieza otro con /repasar." and calls == [
        "backlog: repaso (2 juegos)"]
    estados = {r["appid"]: r["estado"] for r in read_backlog(backlog_file)}
    assert estados["6"] == "terminado"  # el doble toque no lo cambió a abandonado


def test_review_with_nothing_pending_shows_current_states():
    s = store()
    for r in s.backlog:
        if r["appid"] in ("5", "6"):
            r["estado"] = "terminado"
    reply = start_review(s, {}, "hades")
    assert reply.text == ("Ya no queda ninguno de «hades» en «pendiente». Están así:\n"
                          "• Hades — ✅ terminado\n• Hades II — ✅ terminado")
    assert start_review(s, {}, "zzzz").text == "No encuentro juegos que casen con «zzzz»."


def test_free_text_status_by_name_or_last_shown():
    r = free_text(store(), "¿cómo tengo los de hades?",
                  llm=fake_llm(request=real_llm.Request(kind="status", game="hades")))
    assert r.text == "• Hades — ⏳ pendiente\n• Hades II — ⏳ pendiente"
    history = [{"user": "(pulsó un botón)", "shown": [("9", "Portal 2")]}]
    r = free_text(store(), "dame sus estados", history=history,
                  llm=fake_llm(request=real_llm.Request(kind="status")))
    assert "1. Portal 2 — comprado el 2019-03-03" in r.text and r.text.endswith("terminado")


def test_confirm_marks_reports_shown(backlog_file):
    reply = confirm_marks([("6", "terminado")], backlog_path=backlog_file, sync=lambda p, m: True)
    assert reply.shown == [("6", "Hades II")]


def test_free_text_help_and_cost():
    assert free_text(store(), "hola", llm=fake_llm(request=real_llm.Request(kind="help", game="ayuda"))
                     ).text.startswith("Te ayudo a elegir")
    assert free_text(store(), "gasto IA", llm=fake_llm(request=real_llm.Request(kind="help", game="coste"))
                     ).text.startswith("IA este mes:")


def test_free_text_status_falls_back_to_last_shown():
    history = [{"user": "(pulsó un botón)", "shown": [("5", "Hades"), ("9", "Portal 2")]}]
    req = real_llm.Request(kind="status", game="Celeste; Portal 2")
    r = free_text(store(), "dame sus estados", history=history, llm=fake_llm(request=req))
    assert r.text == "• Celeste — ⏳ pendiente\n• Portal 2 — ✅ terminado"  # «;» separa nombres
    r = free_text(store(), "dame sus estados", history=history,
                  llm=fake_llm(request=real_llm.Request(kind="status", game="zzzz")))
    assert r.text.startswith("Así están:\n1. Hades") and "2. Portal 2" in r.text
    r = free_text(store(), "¿y sus estados?", llm=fake_llm(request=real_llm.Request(kind="status")))
    assert r.text == "¿De qué juegos? Dime un nombre o una saga."


def test_free_text_source_family_only():
    req = real_llm.Request(filters=Filters(limit=5), source="familiar")
    r = free_text(store(), "de la familiar", llm=fake_llm(request=req))
    assert "Hades" in r.text and "Celeste" in r.text and "Stardew" not in r.text


def test_replies_carry_header_photos():
    s = store()
    s.games["5"]["images"] = {"header": "https://cdn/5/header.jpg"}
    s.games["6"]["images"] = {"header": "https://cdn/6/header.jpg"}
    assert cmd_juego(s, "hades").photos == [("https://cdn/5/header.jpg", "Hades"),
                                           ("https://cdn/6/header.jpg", "Hades II")]
    rec = cmd_recomienda(s, "")
    assert rec.photos[0] == ("https://cdn/5/header.jpg", "1. Hades")  # sin imagen, no hay foto
    assert all(url.startswith("https://cdn/") for url, _ in rec.photos)
    review = cmd_repasar(started_store_with(s))
    assert review.photos == [("https://cdn/6/header.jpg", "Hades II")]


def started_store_with(s):
    s.library["7"]["hours"] = 5.0
    return s


def deck_store():
    s = store()
    for appid, deck in (("5", 3), ("6", 1), ("7", 2), ("8", 0)):
        s.games[appid]["deck"] = deck
    return s


def test_render_marks_deck_and_early_access():
    recs = [Recommendation("1", "A", 0.9, "x", 5, 90, deck=3),
            Recommendation("2", "B", 0.9, "y", None, 74, deck=2, early_access=True),
            Recommendation("3", "C", 0.9, "z", 5, 90, deck=0)]
    text = render(None, recs)
    assert "1. A — 5.0 h · 90 % 👍 · Deck ✅\n" in text
    assert "2. B — ? h · 74 % 👍 · Deck 🟡 · acceso anticipado\n" in text
    assert "3. C — 5.0 h · 90 % 👍\n" in text


def test_detail_shows_deck_and_release():
    s = store()
    s.games["5"].update(deck=2, early_access=True, release_date="2026-03-12")
    s.games["8"].update(deck=3, release_date="2018-01-25")
    assert "En acceso anticipado desde 2026-03-12 · Steam Deck: 🟡 jugable" in cmd_juego(s, "hades").text
    assert "Salió el 2018-01-25 · Steam Deck: ✅ verificado" in cmd_juego(s, "celeste").text
    s.games["7"].update(coming_soon=True, release_date="2027-02-01")
    assert "Aún no ha salido (prevista para 2027-02-01)" in cmd_juego(s, "stardew").text


def test_free_text_deck_filter_once_does_not_save(tmp_path, monkeypatch):
    monkeypatch.setattr("commands.PREFS_JSON", tmp_path / "prefs.json")
    s = deck_store()
    req = real_llm.Request(filters=Filters(limit=5), deck="jugable")
    reply = free_text(s, "para la deck", llm=fake_llm(request=req))
    assert [n for _, n in reply.shown] == ["Hades", "Stardew Valley"]
    assert not (tmp_path / "prefs.json").exists() and s.deck is None


def test_free_text_deck_remember_saves_and_applies_later(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    monkeypatch.setattr("commands.PREFS_JSON", path)
    s = deck_store()
    req = real_llm.Request(filters=Filters(limit=5), deck="verificado", remember=True)
    reply = free_text(s, "ya sabes que tienen que ir en la deck", llm=fake_llm(request=req))
    assert reply.text.startswith("Apuntado: a partir de ahora solo te recomiendo juegos "
                                 "verificados para Steam Deck")
    assert [n for _, n in reply.shown] == ["Hades"]
    assert json.loads(path.read_text()) == {"deck": "verificado"}
    # Una petición que no habla de la Deck usa la preferencia guardada, y /top también.
    later = free_text(s, "algo", llm=fake_llm(request=real_llm.Request(filters=Filters(limit=5))))
    assert [n for _, n in later.shown] == ["Hades"]
    assert cmd_top(s).text.startswith("(solo verificados para Steam Deck)")
    # «Da igual la Deck» de una vez no la borra; con remember, sí.
    once = free_text(s, "da igual", llm=fake_llm(request=real_llm.Request(
        filters=Filters(limit=5), deck="cualquiera")))
    assert len(once.shown) == 4 and s.deck == "verificado"
    off = free_text(s, "olvida lo de la deck", llm=fake_llm(request=real_llm.Request(
        filters=Filters(limit=5), deck="cualquiera", remember=True)))
    assert off.text.startswith("Apuntado: vuelvo a recomendarte sin mirar la Steam Deck.")
    assert json.loads(path.read_text()) == {} and s.deck is None


def test_free_text_limits_explains_what_bot_cannot_see():
    reply = free_text(store(), "foto de lo instalado en la deck",
                      llm=fake_llm(request=real_llm.Request(kind="help", game="limites")))
    assert reply.text.startswith("Eso no lo puedo saber: Steam no comparte qué tienes instalado")


def test_mostrados_shows_deck_mark():
    s = deck_store()
    text = cmd_mostrados(s, [("5", "Hades"), ("8", "Celeste")]).text
    assert "1. Hades — biblioteca familiar, disponible desde 2021-07-01 · dura 24 h · Deck ✅ ·" in text
    assert "Deck" not in text.splitlines()[2]
