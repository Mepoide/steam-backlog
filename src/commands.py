"""Lógica de los comandos del bot, sin Telegram: cada comando devuelve un Reply."""
import difflib
import json
import re
from dataclasses import dataclass, field

import git_sync
import llm as llm_module
from config import BACKLOG_CSV, GAMES_JSON, SCORES_JSON
from library import ESTADOS, read_backlog, set_estado
from recommender import Filters, Recommendation, fold, netflix_rows, recommend, top_tags

ESTADOS_TXT = "pendiente, jugando, terminado, abandonado, descartado"
NO_DATA = "Aún no hay recomendaciones: ejecuta enrich.py y taste.py (o espera al job diario)."
RECOMIENDA_LIMIT = 5


@dataclass
class Reply:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (etiqueta, callback_data)


@dataclass
class Store:
    games: dict
    scores: dict
    backlog: list[dict]


def _load_json(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_store() -> Store:
    return Store(games=_load_json(GAMES_JSON), scores=_load_json(SCORES_JSON),
                 backlog=read_backlog(BACKLOG_CSV))


def _hours(h: float | None) -> str:
    if h is None:
        return "?"
    return f"{round(h)}" if h >= 10 else f"{h:.1f}"


def render(title: str | None, recs: list[Recommendation]) -> str:
    lines = [title] if title else []
    for i, r in enumerate(recs, 1):
        pct = "?" if r.positive_pct is None else r.positive_pct
        lines += [f"{i}. {r.name} — {_hours(r.hours)} h · {pct} % 👍", f"   {r.reason}", f"   {r.url}"]
    return "\n".join(lines)


def cmd_ayuda() -> Reply:
    return Reply(
        "Te ayudo a elegir qué jugar de tu backlog.\n\n"
        "/top — tus filas: para ti, sesión corta y joyas valoradas\n"
        "/recomienda [duración] [géneros] — p. ej. /recomienda 10h roguelike, estrategia\n"
        "   (la duración es la del juego completo, según HowLongToBeat)\n"
        "/buscar <texto> — busca en el backlog\n"
        f"/marcar <juego> <estado> — estados: {ESTADOS_TXT}\n\n"
        "O escríbeme sin comando: «algo tranquilo, estoy quemado»."
    )


def cmd_top(store: Store) -> Reply:
    rows = netflix_rows(store.games, store.scores, store.backlog)
    if not rows:
        return Reply(NO_DATA)
    return Reply("\n\n".join(render(title, recs) for title, recs in rows))


def parse_recomienda_args(args: str) -> Filters:
    f = Filters(limit=RECOMIENDA_LIMIT)
    rest = args
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(h|horas?|min|minutos?)\b", args, re.IGNORECASE)
    if m:
        value = float(m.group(1).replace(",", "."))
        f.max_hours = value / 60 if m.group(2).lower().startswith("min") else value
        rest = args[:m.start()] + args[m.end():]
    f.include_tags = [t.strip() for t in rest.split(",") if t.strip()]
    return f


def cmd_recomienda(store: Store, args: str) -> Reply:
    recs = recommend(parse_recomienda_args(args), store.games, store.scores, store.backlog)
    if not recs:
        return Reply("Nada encaja con esos filtros. Prueba con más horas o sin género.")
    return Reply(render(None, recs))


def find_games(backlog: list[dict], query: str, n: int = 5) -> list[dict]:
    q = fold(query.strip())
    if not q:
        return []
    exact = [r for r in backlog if fold(r["name"]) == q]
    if exact:
        return exact[:1]
    sub = [r for r in backlog if q in fold(r["name"])]
    if sub:
        return sub[:n]
    names = {fold(r["name"]): r for r in backlog}
    return [names[k] for k in difflib.get_close_matches(q, list(names), n=n, cutoff=0.6)]


def _entry_line(store: Store, row: dict) -> str:
    g = store.games.get(row["appid"], {})
    total = g.get("reviews_total")
    pct = f"{round(100 * g['reviews_positive'] / total)}" if total else "?"
    return f"{row['name']} — {row['estado']} · {_hours(g.get('hltb_main'))} h · {pct} % 👍"


def cmd_buscar(store: Store, args: str) -> Reply:
    if not args.strip():
        return Reply("Uso: /buscar <texto>")
    rows = find_games(store.backlog, args, n=10)
    if not rows:
        return Reply(f"No encuentro «{args.strip()}» en el backlog.")
    return Reply("\n".join(_entry_line(store, r) for r in rows))


def cmd_marcar(store: Store, args: str) -> Reply:
    words = args.split()
    if len(words) < 2:
        return Reply(f"Uso: /marcar <juego> <estado>. Estados: {ESTADOS_TXT}.")
    estado, name = fold(words[-1]), " ".join(words[:-1])
    if estado not in ESTADOS:
        return Reply(f"Estado no válido: «{words[-1]}». Usa: {ESTADOS_TXT}.")
    found = find_games(store.backlog, name)
    if not found:
        return Reply(f"No encuentro «{name}» en el backlog.")
    cancel = ("Cancelar", "cancel")
    if len(found) == 1:
        row = found[0]
        return Reply(f"¿Marcar «{row['name']}» como {estado}?",
                     [("Sí", f"mark:{row['appid']}:{estado}"), cancel])
    return Reply(f"¿Cuál quieres marcar como {estado}?",
                 [(r["name"], f"mark:{r['appid']}:{estado}") for r in found] + [cancel])


def confirm_mark(appid: str, estado: str, backlog_path=BACKLOG_CSV,
                 sync=git_sync.commit_and_push) -> Reply:
    row = next((r for r in read_backlog(backlog_path) if r["appid"] == appid), None)
    if row is None:
        return Reply("Ese juego ya no está en el backlog.")
    try:
        set_estado(appid, estado, backlog_path)
    except ValueError:
        return Reply(f"Estado no válido: «{estado}».")
    text = f"✓ «{row['name']}» marcado como {estado}."
    if not sync(["backlog.csv"], f"backlog: {row['name']} → {estado}"):
        text += " (no se pudo subir a GitHub, queda en local)"
    return Reply(text)


def free_text(store: Store, text: str, llm=llm_module) -> Reply:
    args = (store.games, store.scores, store.backlog)
    try:
        filters = llm.parse_request(text, top_tags(store.games))
    except llm.LLMError:
        recs = recommend(Filters(), *args)
        if not recs:
            return Reply(NO_DATA)
        return Reply("No he podido usar la IA ahora mismo; aquí van tus recomendaciones de siempre:\n\n"
                     + render(None, recs))
    recs = recommend(filters, *args)
    if not recs:
        recs = recommend(Filters(limit=filters.limit), *args)
        if not recs:
            return Reply(NO_DATA)
        return Reply("No he encontrado nada exacto; esto es lo que más te encaja en general:\n\n"
                     + render(None, recs))
    try:
        intro = llm.write_intro(text, recs)
    except llm.LLMError:
        return Reply(render(None, recs))
    return Reply(f"{intro}\n\n{render(None, recs)}")
