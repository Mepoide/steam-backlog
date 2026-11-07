from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from llm import MODEL, LLMError, parse_request, write_intro
from recommender import Filters, Recommendation

KNOWN = ["Relajantes", "Roguelike", "Estrategia", "Terror"]


class FakeClient:
    def __init__(self, content=None, error=None):
        self.kwargs = None
        self._content, self._error = content or [], error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.kwargs = kw
        if self._error:
            raise self._error
        return SimpleNamespace(content=self._content, stop_reason="end_turn")


def tool_use(inp):
    return [SimpleNamespace(type="tool_use", name="set_filters", input=inp)]


def test_model_is_haiku():
    assert MODEL == "claude-haiku-5-5"


def test_parse_request_maps_tool_input():
    c = FakeClient(tool_use({"max_hours": 10, "include_tags": ["Relajantes"]}))
    assert parse_request("algo tranquilo de 10 horas", KNOWN, client=c) == Filters(
        max_hours=10, include_tags=["Relajantes"])


def test_parse_request_drops_unknown_tags_and_clamps_limit():
    c = FakeClient(tool_use({"include_tags": ["Inventado", "roguelike"], "exclude_tags": ["Nada"],
                             "limit": 9, "min_positive": 1.7}))
    f = parse_request("x", KNOWN, client=c)
    assert f.include_tags == ["roguelike"] and f.exclude_tags == []
    assert f.limit == 5 and f.min_positive == 1.0


def test_parse_request_forces_tool_and_model():
    c = FakeClient(tool_use({}))
    parse_request("x", KNOWN, client=c)
    assert c.kwargs["model"] == MODEL
    assert c.kwargs["tool_choice"] == {"type": "tool", "name": "set_filters"}
    assert "Relajantes" in c.kwargs["system"]


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
