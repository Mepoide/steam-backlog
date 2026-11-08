"""Control de coste de las llamadas al LLM.

Mismo patrón que cbrpnk (relay/src/llm/pricing.ts y token-budget.ts):
- se registra el `usage` REAL que devuelve la API en cada llamada, no una estimación;
- la tabla de precios solo tiene los modelos que usamos. Un modelo que no esté aquí no
  se valora "a ojo": su coste es None y se cuenta aparte (`unpriced_calls`), para que
  la cifra o es correcta o se sabe que falta;
- un tope mensual en dólares con niveles ok / warn (≥ 80 %) / forced (≥ 100 %). En
  forced, llm.py deja de llamar a la API y el bot sigue funcionando sin IA.

El registro es data/llm_usage.jsonl (fuera de git), una línea por llamada.
"""
import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from config import LLM_USAGE

# $ por millón de tokens. Haiku 5.5 cobra más por encima de 100K tokens de prompt.
PRICING = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "long_input": 0.50, "long_output": 2.50},
}
LONG_CONTEXT_TOKENS = 100_000
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0
PER_MTOK = 1_000_000

DEFAULT_BUDGET_USD = 1.0
WARN_RATIO = 0.8

PATH = LLM_USAGE  # los tests lo redirigen a un tmp
_lock = threading.Lock()


@dataclass
class Usage:
    model: str
    kind: str  # "parse" | "intro"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_5m_tokens: int = 0
    cache_1h_tokens: int = 0


@dataclass
class MonthStats:
    calls: int  # todas, con o sin precio
    input_tokens: int
    output_tokens: int
    cost_usd: float  # solo llamadas con precio conocido
    unpriced_calls: int


def _n(obj, name) -> int:
    return int(getattr(obj, name, 0) or 0)


def from_response(model: str, kind: str, resp) -> Usage:
    us = getattr(resp, "usage", None)
    if us is None:
        return Usage(model=model, kind=kind)
    breakdown = getattr(us, "cache_creation", None)
    c1 = _n(breakdown, "ephemeral_1h_input_tokens") if breakdown else 0
    c5 = _n(breakdown, "ephemeral_5m_input_tokens") if breakdown else _n(us, "cache_creation_input_tokens")
    return Usage(model=model, kind=kind, input_tokens=_n(us, "input_tokens"),
                 output_tokens=_n(us, "output_tokens"),
                 cache_read_tokens=_n(us, "cache_read_input_tokens"),
                 cache_5m_tokens=c5, cache_1h_tokens=c1)


def cost_usd(u: Usage) -> float | None:
    price = PRICING.get(u.model)
    if price is None:
        return None
    prompt = u.input_tokens + u.cache_read_tokens + u.cache_5m_tokens + u.cache_1h_tokens
    long = prompt > LONG_CONTEXT_TOKENS
    in_rate = (price["long_input"] if long else price["input"]) / PER_MTOK
    out_rate = (price["long_output"] if long else price["output"]) / PER_MTOK
    return (u.input_tokens * in_rate
            + u.cache_read_tokens * in_rate * CACHE_READ_MULTIPLIER
            + u.cache_5m_tokens * in_rate * CACHE_WRITE_5M_MULTIPLIER
            + u.cache_1h_tokens * in_rate * CACHE_WRITE_1H_MULTIPLIER
            + u.output_tokens * out_rate)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def record(u: Usage, path=None, now: datetime | None = None) -> None:
    path = path or PATH
    line = {"ts": _now(now).isoformat(timespec="seconds"), **asdict(u), "cost_usd": cost_usd(u)}
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def month_stats(path=None, now: datetime | None = None) -> MonthStats:
    path = path or PATH
    month = _now(now).strftime("%Y-%m")
    stats = MonthStats(calls=0, input_tokens=0, output_tokens=0, cost_usd=0.0, unpriced_calls=0)
    if not path.exists():
        return stats
    fields = set(Usage.__dataclass_fields__)
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
            if not row["ts"].startswith(month):
                continue
            u = Usage(**{k: v for k, v in row.items() if k in fields})
        except (ValueError, KeyError, TypeError):
            continue
        stats.input_tokens += u.input_tokens + u.cache_read_tokens + u.cache_5m_tokens + u.cache_1h_tokens
        stats.output_tokens += u.output_tokens
        stats.calls += 1
        cost = cost_usd(u)  # se recalcula con la tabla actual, como en cbrpnk
        if cost is None:
            stats.unpriced_calls += 1
        else:
            stats.cost_usd += cost
    return stats


def budget_usd() -> float:
    raw = os.environ.get("LLM_MONTHLY_BUDGET_USD", "").strip().replace(",", ".")
    try:
        return float(raw) if raw else DEFAULT_BUDGET_USD
    except ValueError:
        return DEFAULT_BUDGET_USD


def budget_level(stats: MonthStats, budget: float) -> str:
    if budget <= 0:
        return "ok"
    ratio = stats.cost_usd / budget
    return "forced" if ratio >= 1 else "warn" if ratio >= WARN_RATIO else "ok"
