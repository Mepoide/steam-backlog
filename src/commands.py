"""Lógica de los comandos del bot, sin Telegram: cada comando devuelve un Reply."""
import difflib
import json
import logging
import re
from dataclasses import dataclass, field

import git_sync
import llm as llm_module
import usage
from config import BACKLOG_CSV, GAMES_JSON, SCORES_JSON
from library import ESTADOS, read_backlog, read_library, set_estado
from recommender import (STARTED_HOURS, Filters, Recommendation, fold, netflix_rows, recommend,
                         top_tags)

log = logging.getLogger(__name__)
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
    library: dict = field(default_factory=dict)  # export crudo: origen, horas y fechas


def _load_json(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_store() -> Store:
    return Store(games=_load_json(GAMES_JSON), scores=_load_json(SCORES_JSON),
                 backlog=read_backlog(BACKLOG_CSV), library=read_library())


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
        "/juego <nombre> — ficha: horas jugadas, fechas, duración y reseñas\n"
        "/recientes — lo último que ha llegado a tu biblioteca\n"
        f"/marcar <juego> <estado> — estados: {ESTADOS_TXT}\n"
        "/coste — lo que lleva gastado la IA este mes\n\n"
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


def _game_matches(backlog: list[dict], query: str, n: int = 3) -> list[dict]:
    """Exacto primero y luego los que lo contienen: «hades» → Hades y Hades II."""
    q = fold(query.strip())
    exact = [r for r in backlog if fold(r["name"]) == q]
    sub = [r for r in backlog if q in fold(r["name"]) and r not in exact]
    return (exact + sub)[:n] or find_games(backlog, query, n=n)


def _played(hours: float | None) -> str:
    return "sin jugar" if not hours else f"{hours:.1f} h"


def _detail(store: Store, row: dict) -> str:
    appid = row["appid"]
    g, lib = store.games.get(appid, {}), store.library.get(appid, {})
    lines = [f"{row['name']} — {row['estado']}"]
    hours, last, acquired = lib.get("hours"), lib.get("last_played"), lib.get("acquired")
    played = (f"{hours:.1f} h jugadas" + (f" (última vez {last})" if last else "")) if hours else "Sin jugar"
    if lib.get("source") == "compartido":
        origin = "biblioteca familiar" + (f", disponible desde {acquired}" if acquired else "")
    elif lib:
        origin = "comprado" + (f" el {acquired}" if acquired else "")
    else:
        origin = ""
    lines.append(played + (f" · {origin}" if origin else ""))
    if g.get("hltb_main"):
        extra = f" · {_hours(g['hltb_extra'])} h con extras" if g.get("hltb_extra") else ""
        lines.append(f"Duración: {_hours(g['hltb_main'])} h historia principal (HowLongToBeat){extra}")
    if g.get("reviews_total"):
        pct = round(100 * g["reviews_positive"] / g["reviews_total"])
        lines.append(f"Reseñas: {pct} % 👍 de {g['reviews_total']}")
    tags = sorted((g.get("tags") or {}).items(), key=lambda kv: -kv[1])[:6]
    if tags:
        lines.append("Tags: " + ", ".join(t for t, _ in tags))
    if row["estado"] == "pendiente" and (hours or 0) >= STARTED_HOURS:
        lines.append(f"Sigue en «pendiente» y no te lo recomiendo: /marcar {row['name']} jugando "
                     "(o terminado)")
    lines.append(f"https://store.steampowered.com/app/{appid}")
    return "\n".join(lines)


def cmd_juego(store: Store, args: str) -> Reply:
    if not args.strip():
        return Reply("Uso: /juego <nombre>")
    rows = _game_matches(store.backlog, args)
    if not rows:
        return Reply(f"No encuentro «{args.strip()}» en tu biblioteca.")
    return Reply("\n\n".join(_detail(store, r) for r in rows))


def cmd_recientes(store: Store, limit: int = 10) -> Reply:
    estado = {r["appid"]: r["estado"] for r in store.backlog}
    dated = sorted(((v["acquired"], k, v) for k, v in store.library.items() if v.get("acquired")),
                   key=lambda t: t[0], reverse=True)[:limit]
    if not dated:
        return Reply("No tengo las fechas de tu biblioteca: falta el export (export.py o el job diario).")
    lines = ["Últimos juegos añadidos a tu biblioteca:"]
    for i, (acq, appid, v) in enumerate(dated, 1):
        origin = "familiar" if v.get("source") == "compartido" else "propio"
        lines.append(f"{i}. {v['name']} — {acq} · {origin} · {_played(v.get('hours'))} · "
                     f"{estado.get(appid, 'fuera del backlog')}")
    return Reply("\n".join(lines))


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


def _k(n: int) -> str:
    return f"{n / 1000:.1f}K"


def cmd_coste(budget: float | None = None) -> Reply:
    stats = usage.month_stats()
    budget = usage.budget_usd() if budget is None else budget
    lines = [f"IA este mes: {stats.calls} llamadas · {_k(stats.input_tokens)} tokens de entrada · "
             f"{_k(stats.output_tokens)} de salida"]
    if budget > 0:
        lines.append(f"Coste: {stats.cost_usd:.4f} $ de {budget:.2f} $ "
                     f"({round(100 * stats.cost_usd / budget)} %). Al llegar al tope sigo sin IA.")
    else:
        lines.append(f"Coste: {stats.cost_usd:.4f} $ (sin tope: LLM_MONTHLY_BUDGET_USD=0)")
    if stats.unpriced_calls:
        lines.append(f"Ojo: {stats.unpriced_calls} llamadas sin precio conocido no suman al coste.")
    return Reply("\n".join(lines))


def free_text(store: Store, text: str, llm=llm_module, previous: str | None = None) -> Reply:
    args = (store.games, store.scores, store.backlog)
    if not store.scores:
        return Reply(NO_DATA)  # sin datos no merece la pena gastar una llamada
    try:
        request = llm.parse_request(text, top_tags(store.games), previous=previous)
    except llm.LLMError as e:
        log.warning("LLM (parse_request) falló: %s", e)
        recs = recommend(Filters(), *args)
        if not recs:
            return Reply(NO_DATA)
        why = ("Este mes ya se ha gastado el presupuesto de IA (/coste)"
               if isinstance(e, llm_module.BudgetExceeded) else "No he podido usar la IA ahora mismo")
        return Reply(f"{why}; aquí van tus recomendaciones de siempre:\n\n" + render(None, recs))
    if request.kind == "game":
        log.info("Texto libre «%s» → ficha de «%s»", text, request.game)
        return cmd_juego(store, request.game)
    if request.kind == "recent":
        log.info("Texto libre «%s» → recientes (%d)", text, request.limit)
        return cmd_recientes(store, request.limit)
    filters = request.filters
    recs = recommend(filters, *args)
    log.info("Texto libre «%s» → %s → %s", text, filters, [r.name for r in recs])
    if not recs:
        recs = recommend(Filters(limit=filters.limit), *args)
        if not recs:
            return Reply(NO_DATA)
        return Reply("No he encontrado nada exacto; esto es lo que más te encaja en general:\n\n"
                     + render(None, recs))
    try:
        intro = llm.write_intro(text, recs)
    except llm.LLMError as e:
        log.warning("LLM (write_intro) falló: %s", e)
        return Reply(render(None, recs))
    return Reply(f"{intro}\n\n{render(None, recs)}")
