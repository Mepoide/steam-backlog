import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import usage
from usage import Usage, budget_level, cost_usd, from_response, month_stats, record

NOV = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)


def u(model="claude-haiku-5-5", inp=0, out=0, read=0, c5=0, c1=0, kind="parse"):
    return Usage(model=model, kind=kind, input_tokens=inp, output_tokens=out,
                 cache_read_tokens=read, cache_5m_tokens=c5, cache_1h_tokens=c1)


def test_cost_haiku_input_and_output():
    # 100K es justo el límite de la tarifa normal ($0.10 / $0.50 por MTok).
    assert cost_usd(u(inp=100_000)) == pytest.approx(0.01)
    assert cost_usd(u(out=1_000_000)) == pytest.approx(0.50)


def test_cost_cache_multipliers():
    assert cost_usd(u(read=100_000)) == pytest.approx(0.001)
    assert cost_usd(u(c5=100_000)) == pytest.approx(0.0125)
    assert cost_usd(u(c1=100_000)) == pytest.approx(0.02)


def test_cost_long_prompt_uses_long_context_rates():
    # Más de 100K tokens de prompt (contando caché): $0.50 / $2.50 por MTok.
    assert cost_usd(u(inp=200_000, out=1_000)) == pytest.approx(0.2 * 0.50 + 0.001 * 2.50)


def test_unknown_model_has_no_price():
    assert cost_usd(u(model="claude-inventado")) is None


def test_from_response_reads_usage_and_tolerates_missing_fields():
    resp = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=100, output_tokens=20, cache_read_input_tokens=None,
        cache_creation_input_tokens=30,
        cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=10, ephemeral_1h_input_tokens=20)))
    got = from_response("claude-haiku-5-5", "intro", resp)
    assert got == u(inp=100, out=20, c5=10, c1=20, kind="intro")
    assert from_response("m", "parse", SimpleNamespace()) == u(model="m")
    # Sin desglose por TTL, toda la creación de caché se cuenta a 5 min.
    legacy = SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                                   cache_creation_input_tokens=7))
    assert from_response("m", "parse", legacy).cache_5m_tokens == 7


def test_record_and_month_stats(tmp_path):
    path = tmp_path / "llm_usage.jsonl"
    record(u(inp=1_000_000), path, now=datetime(2026, 10, 31, 23, tzinfo=timezone.utc))  # otro mes
    record(u(inp=100_000, out=1_000_000), path, now=NOV)
    record(u(model="claude-inventado", inp=5), path, now=NOV)
    stats = month_stats(path, now=NOV)
    assert stats.calls == 2 and stats.unpriced_calls == 1
    assert stats.input_tokens == 100_005 and stats.output_tokens == 1_000_000
    assert stats.cost_usd == pytest.approx(0.51)
    line = json.loads(path.read_text().splitlines()[1])
    assert line["kind"] == "parse" and line["cost_usd"] == pytest.approx(0.51)


def test_month_stats_without_file_or_with_corrupt_lines(tmp_path):
    assert month_stats(tmp_path / "no.jsonl", now=NOV).calls == 0
    path = tmp_path / "u.jsonl"
    path.write_text("no es json\n")
    record(u(inp=10), path, now=NOV)
    assert month_stats(path, now=NOV).calls == 1


def test_budget_levels():
    s = lambda c: usage.MonthStats(calls=1, input_tokens=0, output_tokens=0, cost_usd=c, unpriced_calls=0)
    assert budget_level(s(0.5), 1.0) == "ok"
    assert budget_level(s(0.8), 1.0) == "warn"
    assert budget_level(s(1.0), 1.0) == "forced"
    assert budget_level(s(5.0), 0) == "ok"  # 0 = sin tope


def test_budget_from_env(monkeypatch):
    monkeypatch.delenv("LLM_MONTHLY_BUDGET_USD", raising=False)
    assert usage.budget_usd() == usage.DEFAULT_BUDGET_USD
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "2,5")
    assert usage.budget_usd() == 2.5
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "mucho")
    assert usage.budget_usd() == usage.DEFAULT_BUDGET_USD
