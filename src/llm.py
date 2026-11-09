"""Capa conversacional con Claude: texto libre → filtros, y una intro breve.

El LLM nunca elige ni puntúa juegos; eso lo hace recommender.py.
"""
import logging
import re
from dataclasses import dataclass, field

import anthropic

import usage
from recommender import Filters, Recommendation, matches

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
MAX_LIMIT = 5
MAX_RECENT = 20
MAX_MARKS = 5  # caben en el callback_data de un botón de Telegram (64 bytes)
ESTADOS = ["pendiente", "jugando", "terminado", "abandonado", "descartado"]

FILTERS_TOOL = {
    "name": "set_filters",
    "description": "Recomendar qué jugar del backlog del usuario, con filtros.",
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
            "like_game": {"type": "string",
                          "description": "Si pide juegos parecidos a uno concreto, su nombre."},
            "source": {"type": "string", "enum": ["propio", "familiar"],
                       "description": "Solo juegos propios o solo de la biblioteca familiar, si lo pide."},
            "sort": {"type": "string", "enum": ["gusto", "valoracion", "duracion"],
                     "description": ("Orden: gusto (por defecto), valoracion (mejor valorados por "
                                     "reseñas) o duracion (más cortos primero).")},
        },
        "additionalProperties": False,
    },
}

MARK_TOOL = {
    "name": "mark_games",
    "description": ("Cambiar el estado de juegos de su backlog cuando cuenta que los ha terminado, "
                    "está con ellos, los dejó o no le interesan, o pide marcarlos."),
    "input_schema": {
        "type": "object",
        "properties": {
            "games": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Nombre del juego."},
                               "estado": {"type": "string", "enum": ESTADOS}},
                "required": ["name", "estado"],
                "additionalProperties": False,
            }},
        },
        "required": ["games"],
        "additionalProperties": False,
    },
}

LAST_TOOL = {
    "name": "last_results",
    "description": ("Preguntas sobre los juegos que el bot acaba de mostrar: si los tiene, si son "
                    "propios o de la biblioteca familiar, cuánto duran, cuánto los ha jugado."),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

GAME_TOOL = {
    "name": "game_info",
    "description": ("Ficha de juegos concretos de su biblioteca: horas jugadas, última partida, "
                    "fecha de compra, duración, reseñas y tags. Para preguntas sobre juegos que nombra."),
    "input_schema": {
        "type": "object",
        "properties": {"names": {"type": "array", "items": {"type": "string"},
                                 "description": "Nombre de cada juego, como lo escribe (1-5)."}},
        "required": ["names"],
        "additionalProperties": False,
    },
}

STATUS_TOOL = {
    "name": "game_status",
    "description": ("Ver en qué estado (pendiente, jugando, terminado, abandonado, descartado) "
                    "están juegos de su backlog: los de un nombre o saga, o los que acaba de "
                    "mostrar o tocar el bot si no dice cuáles."),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string",
                                 "description": ("UN nombre o saga («sherlock holmes»). Vacío si se "
                                                 "refiere a los últimos juegos que mostró el bot.")}},
        "additionalProperties": False,
    },
}

HELP_TOOL = {
    "name": "bot_help",
    "description": ("Preguntas sobre el propio bot: saludos, qué puede hacer, cómo se usa (ayuda) o "
                    "cuánto lleva gastado en IA (coste)."),
    "input_schema": {
        "type": "object",
        "properties": {"topic": {"type": "string", "enum": ["ayuda", "coste"]}},
        "required": ["topic"],
        "additionalProperties": False,
    },
}

REVIEW_TOOL = {
    "name": "review_games",
    "description": ("Repasar uno a uno, con botones para cambiar su estado, los juegos que empezó y "
                    "siguen en pendiente; o solo los de un nombre o saga si lo dice."),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string",
                                 "description": "Nombre o saga a repasar («sherlock holmes»); vacío = todos."}},
        "additionalProperties": False,
    },
}

RECENT_TOOL = {
    "name": "recent_games",
    "description": ("Juegos añadidos a su biblioteca más recientemente, por fecha de compra "
                    "(o de llegada a la biblioteca familiar)."),
    "input_schema": {
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "Cuántos juegos, 1-20."}},
        "additionalProperties": False,
    },
}

PARSE_SYSTEM = """Atiendes a un jugador que pregunta por su biblioteca de Steam.
Llama siempre a UNA herramienta:
- game_info si pregunta por juegos concretos (cuánto los ha jugado, qué tal son, cuánto duran...);
  pon cada juego que nombre por separado y con su nombre completo («awakened» de Sherlock
  Holmes → «Sherlock Holmes The Awakened»); la saga sola no es un juego.
- review_games solo si quiere ponerse a clasificar o cambiar el estado de sus juegos uno a uno
  («repasemos los de Sherlock Holmes», «vamos a clasificar lo que tengo empezado»).
- game_status si pregunta en qué estado están («¿qué estados tienen?», «¿cómo tengo los de
  Sherlock?», «dame sus estados»); sin nombre se refiere a los últimos que mostró o tocó el bot.
- recent_games si pregunta qué ha comprado o se ha añadido hace poco a su biblioteca.
- mark_games si cuenta cómo le va con juegos o pide marcarlos: terminado (se lo ha pasado),
  jugando (está con él), abandonado (lo empezó y lo dejó o le aburre), descartado (no lo ha empezado
  y no le interesa),
  pendiente (aún quiere jugarlo). Incluye todos los que mencione, cada uno con su estado.
- last_results si pregunta algo sobre los juegos que el bot acaba de mostrar
  («¿esos los tengo?», «¿cuánto duran?», «¿cuáles son de la familiar?»).
- bot_help si saluda sin pedir nada, pregunta qué puede hacer el bot o cuánto ha gastado en IA.
- set_filters para todo lo demás: qué jugar, recomendaciones, géneros, duraciones.
Te doy la conversación reciente para entender el mensaje actual: «¿y de estrategia?» sigue
recomendando; si el actual aclara o corrige el anterior («fecha de compra me refiero» tras
preguntar qué se ha añadido hace poco), mantén la misma herramienta que pedía el anterior.
Si se refiere a juegos que mostró el bot («los dos últimos», «los que te he dicho»), usa sus
nombres tal cual aparecen en «Bot mostró».
Reglas de set_filters:
- max_hours es la duración TOTAL del juego, no la de la sesión. Úsala solo si el usuario
  habla de juegos cortos o de terminar algo en poco tiempo.
- like_game: si pide algo «parecido a», «como» o «del estilo de» un juego, pon su nombre.
- source: «de la biblioteca familiar» / «de los compartidos» → familiar; «de los míos» → propio.
- sort: «el mejor valorado», «lo mejor que tengo» → valoracion; «lo más corto» → duracion.
- Traduce lo que pide a tags usando SOLO esta lista (copia los nombres tal cual):
{tags}
- En include_tags pon de 1 a 3 tags, los más específicos que describan la petición
  (p. ej. «tipo granja» → Agricultura, Simulador de vida; «tranquilo» → Relajantes, Acogedores;
  «rol por turnos» → Combate por turnos, Juegos de rol tácticos; mejor que Rol y Por turnos
  sueltos, que también casan con estrategia).
  No añadas tags genéricos (Acción, Aventura, Casuales, Indie, Un jugador, Multijugador...)
  salvo que sean justo lo que pide: «con amigos» sí es Cooperativos en línea o Multijugador.
- Lo que el usuario quiera evitar va en exclude_tags.
- Si no hay una petición clara de algo, deja el campo vacío. No inventes tags."""

INTRO_SYSTEM = """Eres el bot de backlog de Steam de un jugador. Escribe en español una
introducción de una o dos frases, cercana, a la lista de juegos que se va a mostrar justo
debajo. La lista son recomendaciones de juegos que aún no ha empezado, elegidas por su
gusto y los filtros de la petición: no digas que es otra cosa ni inventes datos sobre
ellos. Si algún juego no encaja con lo pedido, dilo con naturalidad. No nombres ningún
juego que no esté en la lista ni hables de lo que puedes o no puedes hacer. Sin listas
ni formato."""


@dataclass
class Request:
    """Lo que pide el usuario: recomendar, una ficha, los recientes, marcar o la última lista."""
    kind: str = "recommend"  # recommend | game | recent | mark | last | review | status | help
    filters: Filters = field(default_factory=Filters)
    game: str = ""  # referencia de «parecido a» («recommend») o filtro del repaso («review»)
    games: list[str] = field(default_factory=list)  # fichas pedidas («game»)
    limit: int = 10
    marks: list[tuple[str, str]] = field(default_factory=list)  # (nombre, estado)
    source: str = ""  # "propio" | "familiar" (filtro de recomendación)


@dataclass
class Turn:
    """Un intercambio anterior: lo que escribió el usuario y los juegos que mostró el bot."""
    user: str
    shown: list[str] = field(default_factory=list)


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    """Tope mensual de gasto alcanzado: no se llama a la API hasta el mes que viene."""


def _check_budget() -> None:
    stats, budget = usage.month_stats(), usage.budget_usd()
    level = usage.budget_level(stats, budget)
    if level == "forced":
        raise BudgetExceeded(f"presupuesto mensual de IA agotado ({stats.cost_usd:.2f} de {budget:.2f} $)")
    if level == "warn":
        log.warning("Gasto de IA del mes al %d %% (%.3f de %.2f $)",
                    100 * stats.cost_usd / budget, stats.cost_usd, budget)


def _create(client, kind: str, **kwargs):
    _check_budget()
    try:
        c = client if client is not None else anthropic.Anthropic()
        resp = c.messages.create(**kwargs)
    except (anthropic.AnthropicError, TypeError) as e:  # sin credenciales el SDK lanza TypeError
        raise LLMError(f"{type(e).__name__}: {e}") from e
    try:
        usage.record(usage.from_response(kwargs["model"], kind, resp))
    except OSError:
        log.exception("No se pudo registrar el uso del LLM")
    return resp


def _known(tags: list[str], known: list[str]) -> list[str]:
    known_entry = {"tags": {k: 1 for k in known}}
    return [t for t in tags if isinstance(t, str) and t.strip() and matches(known_entry, t)]


def _filters(inp: dict, known_tags: list[str]) -> Filters:
    f = Filters(include_tags=_known(inp.get("include_tags") or [], known_tags),
                exclude_tags=_known(inp.get("exclude_tags") or [], known_tags))
    if isinstance(inp.get("max_hours"), (int, float)) and inp["max_hours"] > 0:
        f.max_hours = inp["max_hours"]
    if isinstance(inp.get("min_positive"), (int, float)):
        f.min_positive = min(max(float(inp["min_positive"]), 0.0), 1.0)
    if isinstance(inp.get("limit"), int):
        f.limit = min(max(inp["limit"], 1), MAX_LIMIT)
    return f


def _user_content(text: str, history: list[Turn] | None) -> str:
    if not history:
        return text
    lines = ["Conversación reciente (solo contexto):"]
    for t in history:
        lines.append(f"Usuario: {t.user}")
        if t.shown:
            lines.append("Bot mostró: " + "; ".join(t.shown))
    return "\n".join(lines) + f"\nMensaje actual: {text}"


def _marks(inp: dict) -> list[tuple[str, str]]:
    out = []
    for g in inp.get("games") or []:
        if not isinstance(g, dict):
            continue
        name, estado = g.get("name"), g.get("estado")
        if isinstance(name, str) and name.strip() and estado in ESTADOS:
            out.append((name.strip(), estado))
    return out[:MAX_MARKS]


TOOLS = [FILTERS_TOOL, GAME_TOOL, RECENT_TOOL, MARK_TOOL, LAST_TOOL, REVIEW_TOOL, STATUS_TOOL,
         HELP_TOOL]
MAX_GAMES = 5


def parse_request(text: str, known_tags: list[str], client=None,
                  history: list[Turn] | None = None) -> Request:
    resp = _create(
        client, "parse", model=MODEL, max_tokens=1000,
        system=PARSE_SYSTEM.format(tags=", ".join(known_tags)),
        tools=TOOLS, tool_choice={"type": "any"},
        messages=[{"role": "user", "content": _user_content(text, history)}],
    )
    names = {t["name"] for t in TOOLS}
    call = next((b for b in resp.content if b.type == "tool_use" and b.name in names), None)
    if call is None:
        raise LLMError(f"sin llamada a herramienta (stop_reason={resp.stop_reason})")
    inp = call.input or {}
    if call.name == "game_info":
        names = [n.strip() for n in inp.get("names") or [] if isinstance(n, str) and n.strip()]
        if names:
            return Request(kind="game", games=names[:MAX_GAMES])
    if call.name == "bot_help":
        return Request(kind="help", game="coste" if inp.get("topic") == "coste" else "ayuda")
    if call.name in ("review_games", "game_status"):
        query = inp.get("query")
        return Request(kind="review" if call.name == "review_games" else "status",
                       game=query.strip() if isinstance(query, str) else "")
    if call.name == "recent_games":
        limit = inp.get("limit")
        return Request(kind="recent", limit=min(max(limit, 1), MAX_RECENT) if isinstance(limit, int) else 10)
    if call.name == "mark_games" and _marks(inp):
        return Request(kind="mark", marks=_marks(inp))
    if call.name == "last_results":
        return Request(kind="last")
    like = inp.get("like_game")
    filters = _filters(inp, known_tags)
    if inp.get("sort") in ("valoracion", "duracion"):
        filters.sort = inp["sort"]
    return Request(filters=filters, game=like.strip() if isinstance(like, str) else "",
                   source=inp.get("source") if inp.get("source") in ("propio", "familiar") else "")


def write_intro(text: str, recs: list[Recommendation], client=None) -> str:
    listing = "\n".join(
        f"- {r.name}: {r.reason}"
        + (f"; dura {r.hours:.1f} h (historia principal)" if r.hours else "; duración desconocida")
        + (f"; tags: {', '.join(r.tags)}" if r.tags else "")
        for r in recs)
    resp = _create(
        client, "intro", model=MODEL, max_tokens=1000, output_config={"effort": "low"},
        system=INTRO_SYSTEM,
        messages=[{"role": "user", "content": f"Petición: {text}\n\nJuegos:\n{listing}"}],
    )
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    # La lista ya va debajo: si el modelo la repite con viñetas, se quita.
    out = "\n".join(l for l in out.splitlines() if not re.match(r"\s*([-*•]|\d+[.)])\s", l)).strip()
    if not out:
        raise LLMError(f"intro vacía (stop_reason={resp.stop_reason})")
    return out
