from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from llm import MODEL, LLMError, Request, Turn, parse_request, write_intro
from recommender import Filters, Recommendation

KNOWN = ["Relajantes", "Roguelike", "Estrategia", "Terror"]


class FakeClient:
    def __init__(self, content=None, error=None):
        self.kwargs = None
        self._content, self._error = content or [], error
        self._usage = None
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.kwargs = kw
        if self._error:
            raise self._error
        return SimpleNamespace(content=self._content, stop_reason="end_turn", usage=self._usage)


def tool_use(inp, name="set_filters"):
    return [SimpleNamespace(type="tool_use", name=name, input=inp)]


def test_model_is_haiku():
    assert MODEL == "claude-haiku-5-5"


def test_parse_request_maps_tool_input():
    c = FakeClient(tool_use({"max_hours": 10, "include_tags": ["Relajantes"]}))
    assert parse_request("algo tranquilo de 10 horas", KNOWN, client=c) == Request(filters=Filters(
        max_hours=10, include_tags=["Relajantes"]))


def test_parse_request_drops_unknown_tags_and_clamps_limit():
    c = FakeClient(tool_use({"include_tags": ["Inventado", "roguelike"], "exclude_tags": ["Nada"],
                             "limit": 9, "min_positive": 1.7}))
    f = parse_request("x", KNOWN, client=c).filters
    assert f.include_tags == ["roguelike"] and f.exclude_tags == []
    assert f.limit == 5 and f.min_positive == 1.0


def test_parse_request_forces_a_tool_and_model():
    c = FakeClient(tool_use({}))
    parse_request("x", KNOWN, client=c)
    assert c.kwargs["model"] == MODEL
    assert c.kwargs["tool_choice"] == {"type": "any"}
    assert [t["name"] for t in c.kwargs["tools"]] == ["set_filters", "game_info", "recent_games",
                                                      "mark_games", "last_results", "review_games"]
    assert "Relajantes" in c.kwargs["system"]
    assert c.kwargs["messages"][0]["content"] == "x"


def test_parse_request_game_info():
    c = FakeClient(tool_use({"names": [" Hades ", "", "Celeste"]}, name="game_info"))
    assert parse_request("Hades y Celeste", KNOWN, client=c) == Request(kind="game", games=["Hades", "Celeste"])


def test_parse_request_recent_games_clamps_limit():
    assert parse_request("x", KNOWN, client=FakeClient(tool_use({}, name="recent_games"))) == Request(
        kind="recent", limit=10)
    got = parse_request("x", KNOWN, client=FakeClient(tool_use({"limit": 99}, name="recent_games")))
    assert got.limit == 20


def test_parse_request_game_info_without_name_falls_back_to_recommend():
    got = parse_request("x", KNOWN, client=FakeClient(tool_use({"names": [" "]}, name="game_info")))
    assert got.kind == "recommend"


def test_parse_request_sends_history_as_context():
    c = FakeClient(tool_use({}))
    history = [Turn("/juego sherlock", ["Sherlock Holmes Chapter One", "Sherlock Holmes The Awakened"]),
               Turn("algo corto")]
    parse_request("márcalos", KNOWN, client=c, history=history)
    assert c.kwargs["messages"][0]["content"] == (
        "Conversación reciente (solo contexto):\n"
        "Usuario: /juego sherlock\n"
        "Bot mostró: Sherlock Holmes Chapter One; Sherlock Holmes The Awakened\n"
        "Usuario: algo corto\n"
        "Mensaje actual: márcalos")


def test_parse_request_mark_games_validates_and_caps():
    games = [{"name": " Celeste ", "estado": "terminado"}, {"name": "X", "estado": "odiado"},
             {"name": "", "estado": "jugando"}, "basura"] + [{"name": f"G{i}", "estado": "jugando"}
                                                            for i in range(9)]
    got = parse_request("x", KNOWN, client=FakeClient(tool_use({"games": games}, name="mark_games")))
    assert got.kind == "mark" and got.marks[0] == ("Celeste", "terminado") and len(got.marks) == 5
    empty = parse_request("x", KNOWN, client=FakeClient(tool_use({"games": []}, name="mark_games")))
    assert empty.kind == "recommend"


def test_parse_request_last_results_and_like_game():
    assert parse_request("¿esos los tengo?", KNOWN,
                         client=FakeClient(tool_use({}, name="last_results"))).kind == "last"
    got = parse_request("algo como BG3", KNOWN,
                        client=FakeClient(tool_use({"like_game": " Baldur's Gate 3 "})))
    assert got.kind == "recommend" and got.game == "Baldur's Gate 3"


def test_parse_request_without_tool_call_raises():
    with pytest.raises(LLMError):
        parse_request("x", KNOWN, client=FakeClient([SimpleNamespace(type="text", text="hola")]))


def test_client_error_raises_llmerror():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    with pytest.raises(LLMError):
        parse_request("x", KNOWN, client=FakeClient(error=err))


def rec():
    return Recommendation("1", "Hades", 0.9, "porque jugaste X", 23.0, 97)


def test_write_intro_returns_text():
    c = FakeClient([SimpleNamespace(type="thinking", thinking=""),
                    SimpleNamespace(type="text", text="  Te van a encantar.  ")])
    assert write_intro("algo frenético", [rec()], client=c) == "Te van a encantar."
    assert c.kwargs["model"] == MODEL
    assert "Hades" in c.kwargs["messages"][0]["content"]


def test_write_intro_sees_game_tags():
    c = FakeClient([SimpleNamespace(type="text", text="Ok.")])
    r = Recommendation("1", "Mount & Blade", 0.9, "porque jugaste X", 42.0, 91, ["Rol", "Medievales"])
    write_intro("algo de rol", [r], client=c)
    assert "tags: Rol, Medievales" in c.kwargs["messages"][0]["content"]


def test_write_intro_empty_raises():
    with pytest.raises(LLMError):
        write_intro("x", [rec()], client=FakeClient([SimpleNamespace(type="text", text="  ")]))


def test_missing_api_key_raises_llmerror(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")
    with pytest.raises(LLMError):
        parse_request("x", KNOWN)
    with pytest.raises(LLMError):
        write_intro("x", [rec()])


def test_each_call_records_real_usage():
    import usage
    c = FakeClient(tool_use({}))
    c._usage = SimpleNamespace(input_tokens=3000, output_tokens=80)
    parse_request("x", KNOWN, client=c)
    write_intro("x", [rec()], client=FakeClient([SimpleNamespace(type="text", text="Hola.")]))
    stats = usage.month_stats()
    assert stats.calls == 2 and stats.input_tokens == 3000 and stats.output_tokens == 80
    kinds = [line.split('"kind": "')[1].split('"')[0] for line in usage.PATH.read_text().splitlines()]
    assert kinds == ["parse", "intro"]


def test_budget_exhausted_blocks_calls(monkeypatch):
    import usage
    from llm import BudgetExceeded
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0.01")
    usage.record(usage.Usage(model=MODEL, kind="parse", output_tokens=20_000))  # 0.01 $
    c = FakeClient(tool_use({}))
    with pytest.raises(BudgetExceeded) as exc:
        parse_request("x", KNOWN, client=c)
    assert c.kwargs is None  # ni se llegó a llamar a la API
    assert isinstance(exc.value, LLMError) and "0.01" in str(exc.value)



def test_write_intro_drops_repeated_bullet_list():
    c = FakeClient([SimpleNamespace(type="text", text="Tres de rol para ti.\n\n- Mount & Blade\n2. Otro")])
    assert write_intro("rol", [rec()], client=c) == "Tres de rol para ti."


def test_parse_request_review_games():
    got = parse_request("repasemos los de sherlock", KNOWN,
                        client=FakeClient(tool_use({"query": " sherlock holmes "}, name="review_games")))
    assert got == Request(kind="review", game="sherlock holmes")
    assert parse_request("x", KNOWN, client=FakeClient(tool_use({}, name="review_games"))).game == ""
