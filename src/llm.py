"""Capa conversacional con Claude: texto libre → filtros, y una intro breve.

El LLM nunca elige ni puntúa juegos; eso lo hace recommender.py.
"""
import anthropic

from recommender import Filters, Recommendation, matches

MODEL = "claude-haiku-5-5"
MAX_LIMIT = 5

FILTERS_TOOL = {
    "name": "set_filters",
    "description": "Filtros para buscar en el backlog de juegos del usuario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "max_hours": {"type": "number",
                          "description": "Duración total máxima del juego en horas, según HowLongToBeat."},
            "include_tags": {"type": "array", "items": {"type": "string"},
                             "description": "Tags o géneros deseados (de la lista conocida)."},
            "exclude_tags": {"type": "array", "items": {"type": "string"},
                             "description": "Tags o géneros a evitar (de la lista conocida)."},
            "min_positive": {"type": "number", "description": "Fracción mínima de reseñas positivas, 0-1."},
            "limit": {"type": "integer", "description": "Número de juegos a recomendar, 1-5."},
        },
        "additionalProperties": False,
    },
}

PARSE_SYSTEM = """Traduces lo que pide un jugador a filtros para buscar en su backlog de Steam.
Llama siempre a set_filters. Reglas:
- max_hours es la duración TOTAL del juego, no la de la sesión. Úsala solo si el usuario
  habla de juegos cortos o de terminar algo en poco tiempo.
- Traduce el estado de ánimo a tags usando SOLO esta lista (copia los nombres tal cual):
{tags}
- Si no hay una petición clara de algo, deja el campo vacío. No inventes tags."""

INTRO_SYSTEM = """Eres el bot de backlog de Steam de un jugador. Escribe en español una
introducción de una o dos frases, cercana, a la lista de juegos que se va a mostrar justo
debajo. No nombres ningún juego que no esté en la lista. Sin listas ni formato."""


class LLMError(Exception):
    pass


def _client(client):
    return client if client is not None else anthropic.Anthropic()


def _known(tags: list[str], known: list[str]) -> list[str]:
    known_entry = {"tags": {k: 1 for k in known}}
    return [t for t in tags if isinstance(t, str) and t.strip() and matches(known_entry, t)]


def parse_request(text: str, known_tags: list[str], client=None) -> Filters:
    try:
        resp = _client(client).messages.create(
            model=MODEL, max_tokens=1000,
            system=PARSE_SYSTEM.format(tags=", ".join(known_tags)),
            tools=[FILTERS_TOOL], tool_choice={"type": "tool", "name": "set_filters"},
            messages=[{"role": "user", "content": text}],
        )
    except anthropic.AnthropicError as e:
        raise LLMError(f"{type(e).__name__}: {e}") from e
    call = next((b for b in resp.content if b.type == "tool_use" and b.name == "set_filters"), None)
    if call is None:
        raise LLMError(f"sin llamada a set_filters (stop_reason={resp.stop_reason})")
    inp = call.input or {}
    f = Filters(include_tags=_known(inp.get("include_tags") or [], known_tags),
                exclude_tags=_known(inp.get("exclude_tags") or [], known_tags))
    if isinstance(inp.get("max_hours"), (int, float)) and inp["max_hours"] > 0:
        f.max_hours = inp["max_hours"]
    if isinstance(inp.get("min_positive"), (int, float)):
        f.min_positive = min(max(float(inp["min_positive"]), 0.0), 1.0)
    if isinstance(inp.get("limit"), int):
        f.limit = min(max(inp["limit"], 1), MAX_LIMIT)
    return f


def write_intro(text: str, recs: list[Recommendation], client=None) -> str:
    listing = "\n".join(f"- {r.name}: {r.reason}" for r in recs)
    try:
        resp = _client(client).messages.create(
            model=MODEL, max_tokens=1000, output_config={"effort": "low"},
            system=INTRO_SYSTEM,
            messages=[{"role": "user", "content": f"Petición: {text}\n\nJuegos:\n{listing}"}],
        )
    except anthropic.AnthropicError as e:
        raise LLMError(f"{type(e).__name__}: {e}") from e
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not out:
        raise LLMError(f"intro vacía (stop_reason={resp.stop_reason})")
    return out
