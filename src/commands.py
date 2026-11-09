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
CODES = {"p": "pendiente", "j": "jugando", "t": "terminado", "a": "abandonado", "d": "descartado"}
CODE_OF = {v: k for k, v in CODES.items()}  # en los botones: callback_data ≤ 64 bytes
NO_DATA = "Aún no hay recomendaciones: ejecuta enrich.py y taste.py (o espera al job diario)."
RECOMIENDA_LIMIT = 5


@dataclass
class Reply:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (etiqueta, callback_data)
    shown: list[tuple[str, str]] = field(default_factory=list)  # (appid, nombre) mostrados
    columns: int = 1  # botones por fila


def _shown(recs: list[Recommendation]) -> list[tuple[str, str]]:
    return [(r.appid, r.name) for r in recs]


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
        "/parecidos <juego> — lo de tu backlog que más se le parece\n"
        "/recientes — lo último que ha llegado a tu biblioteca\n"
        f"/marcar <juego> <estado> — estados: {ESTADOS_TXT}\n"
        "/repasar [nombre] — clasifica con botones los juegos que empezaste y siguen en pendiente "
        "(o solo los de un nombre o saga)\n"
        "/coste — lo que lleva gastado la IA este mes\n\n"
        "O escríbeme sin comando: «algo tranquilo, estoy quemado», «algo como Hades», "
        "«me he pasado el Celeste»."
    )


def cmd_top(store: Store) -> Reply:
    rows = netflix_rows(store.games, store.scores, store.backlog)
    if not rows:
        return Reply(NO_DATA)
    return Reply("\n\n".join(render(title, recs) for title, recs in rows),
                 shown=[s for _, recs in rows for s in _shown(recs)])


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
    return Reply(render(None, recs), shown=_shown(recs))


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", fold(s))


def _token_matches(backlog: list[dict], query: str) -> list[dict]:
    """Cada palabra de la consulta se parece a una del nombre: «devils daughter» →
    «Sherlock Holmes: The Devil's Daughter», «chapters one» → «... Chapter One»."""
    qw = [w for w in _words(query) if len(w) >= 3]
    if not qw:
        return []
    return [r for r in backlog
            if all(difflib.get_close_matches(w, _words(r["name"]), n=1, cutoff=0.8) for w in qw)]


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
    words = _token_matches(backlog, query)
    if words:
        return words[:n]
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
    return Reply("\n".join(_entry_line(store, r) for r in rows),
                 shown=[(r["appid"], r["name"]) for r in rows])


def _game_matches(backlog: list[dict], query: str, n: int = 3) -> list[dict]:
    """Exacto primero y luego los que lo contienen: «hades» → Hades y Hades II."""
    q = fold(query.strip())
    exact = [r for r in backlog if fold(r["name"]) == q]
    sub = [r for r in backlog if q in fold(r["name"]) and r not in exact]
    return (exact + sub)[:n] or find_games(backlog, query, n=n)


def _played(hours: float | None) -> str:
    return "sin jugar" if not hours else f"{hours:.1f} h"


def _origin(lib: dict) -> str:
    acquired = lib.get("acquired")
    if lib.get("source") == "compartido":
        return "biblioteca familiar" + (f", disponible desde {acquired}" if acquired else "")
    if lib:
        return "comprado" + (f" el {acquired}" if acquired else "")
    return ""


def _detail(store: Store, row: dict, hint: bool = True) -> str:
    appid = row["appid"]
    g, lib = store.games.get(appid, {}), store.library.get(appid, {})
    lines = [f"{row['name']} — {row['estado']}"]
    hours, last = lib.get("hours"), lib.get("last_played")
    played = (f"{hours:.1f} h jugadas" + (f" (última vez {last})" if last else "")) if hours else "Sin jugar"
    origin = _origin(lib)
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
    if hint and row["estado"] == "pendiente" and (hours or 0) >= STARTED_HOURS:
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
    return Reply("\n\n".join(_detail(store, r) for r in rows),
                 shown=[(r["appid"], r["name"]) for r in rows])


def cmd_juegos(store: Store, names: list[str], shown: list[tuple[str, str]] = ()) -> Reply:
    """Fichas de varios juegos nombrados («awakened, chapter one y devils daughter»)."""
    if len(names) == 1:
        return cmd_juego(store, names[0])
    rows, missing = [], []
    for name in names:
        row = _resolve(store, name, list(shown))
        if row is None:
            missing.append(name)
        elif row not in rows:
            rows.append(row)
    parts = [_detail(store, r) for r in rows] + [f"No encuentro «{m}» en tu biblioteca." for m in missing]
    return Reply("\n\n".join(parts), shown=[(r["appid"], r["name"]) for r in rows])


def cmd_parecidos(store: Store, args: str) -> Reply:
    if not args.strip():
        return Reply("Uso: /parecidos <juego>, p. ej. /parecidos Baldur's Gate 3")
    rows = _game_matches(store.backlog, args, n=1)
    if not rows:
        return Reply(f"No encuentro «{args.strip()}» en tu biblioteca.")
    ref = rows[0]
    recs = recommend(Filters(like_appid=ref["appid"], limit=RECOMIENDA_LIMIT),
                     store.games, store.scores, store.backlog)
    if not recs:
        return Reply(f"No tengo nada sin empezar que se parezca a {ref['name']}.")
    return Reply(render(f"Lo más parecido a {ref['name']} que no has empezado:", recs),
                 shown=_shown(recs))


def cmd_mostrados(store: Store, shown: list[tuple[str, str]]) -> Reply:
    """«¿Esos los tengo?»: origen, duración y horas de lo último que se mostró."""
    if not shown:
        return Reply("Aún no te he enseñado ningún juego en esta conversación.")
    estado = {r["appid"]: r["estado"] for r in store.backlog}
    all_owned = all(a in store.library for a, _ in shown)
    lines = ["Sí, todos están en tu biblioteca:" if all_owned else "Los que te acabo de enseñar:"]
    for i, (appid, name) in enumerate(shown, 1):
        lib, g = store.library.get(appid, {}), store.games.get(appid, {})
        parts = [_origin(lib) or "no lo veo en tu biblioteca"]
        if g.get("hltb_main"):
            parts.append(f"dura {_hours(g['hltb_main'])} h")
        parts += [_played(lib.get("hours")), estado.get(appid, "fuera del backlog")]
        lines.append(f"{i}. {name} — " + " · ".join(parts))
    return Reply("\n".join(lines), shown=shown)


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
    return Reply("\n".join(lines), shown=[(appid, v["name"]) for _, appid, v in dated])


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


def _resolve(store: Store, name: str, shown: list[tuple[str, str]]) -> dict | None:
    """Nombre que dio el LLM → fila del backlog, priorizando lo que se acaba de mostrar."""
    q = fold(name)
    by_id = {r["appid"]: r for r in store.backlog}
    near = [a for a, n in shown if fold(n) == q] or [a for a, n in shown if q in fold(n)]
    if len(near) == 1 and near[0] in by_id:
        return by_id[near[0]]
    found = _game_matches(store.backlog, name, n=1)
    return found[0] if found else None


def plan_marks(store: Store, marks: list[tuple[str, str]],
               shown: list[tuple[str, str]] = ()) -> Reply:
    """Cambios de estado pedidos en texto libre: se confirman con un único botón."""
    changes, lines, missing = {}, [], []
    for name, estado in marks:
        row = _resolve(store, name, list(shown))
        if row is None:
            missing.append(name)
        elif row["estado"] != estado and row["appid"] not in changes:
            changes[row["appid"]] = estado
            lines.append(f"• {row['name']}: {row['estado']} → {estado}")
    notes = [f"No encuentro «{m}» en el backlog." for m in missing]
    if not changes:
        return Reply("\n".join(notes) or "Ya estaban así; no hay nada que cambiar.")
    data = "marks:" + ",".join(f"{a}={CODE_OF[e]}" for a, e in changes.items())
    return Reply("\n".join(["¿Hago estos cambios?", *lines, *notes]),
                 [("Sí", data), ("Cancelar", "cancel")])


def parse_marks(data: str) -> list[tuple[str, str]]:
    """«marks:350640=t,1949030=a» (o el antiguo «mark:350640:terminado») → [(appid, estado)]."""
    if data.startswith("mark:"):
        _, appid, estado = (data.split(":", 2) + ["", ""])[:3]
        return [(appid, estado)]
    pairs = []
    for item in data.removeprefix("marks:").split(","):
        appid, _, code = item.partition("=")
        if appid and code in CODES:
            pairs.append((appid, CODES[code]))
    return pairs


def _sync_and_refresh(message: str, sync, refresh) -> str:
    suffix = ""
    if not sync(["backlog.csv"], message):
        suffix = " (no se pudo subir a GitHub, queda en local)"
    if refresh is not None:
        try:
            refresh()  # el perfil aprende de lo terminado y lo abandonado
        except Exception:
            log.exception("No se pudo recalcular el perfil")
    return suffix


def confirm_marks(pairs: list[tuple[str, str]], backlog_path=BACKLOG_CSV,
                  sync=git_sync.commit_and_push, refresh=None) -> Reply:
    names = {r["appid"]: r["name"] for r in read_backlog(backlog_path)}
    done, shown = [], []
    for appid, estado in pairs:
        if appid not in names or estado not in ESTADOS:
            continue
        set_estado(appid, estado, backlog_path)
        done.append((names[appid], estado))
        shown.append((appid, names[appid]))
    if not done:
        return Reply("Ese juego ya no está en el backlog." if len(pairs) == 1
                     else "No he podido marcar ninguno.")
    if len(done) == 1:
        text, msg = f"✓ «{done[0][0]}» marcado como {done[0][1]}.", f"backlog: {done[0][0]} → {done[0][1]}"
    else:
        text = "✓ Marcados:\n" + "\n".join(f"• {n} → {e}" for n, e in done)
        msg = "backlog: " + ", ".join(f"{n} → {e}" for n, e in done)
    return Reply(text + _sync_and_refresh(msg, sync, refresh), shown=shown)


def confirm_mark(appid: str, estado: str, backlog_path=BACKLOG_CSV,
                 sync=git_sync.commit_and_push, refresh=None) -> Reply:
    if estado not in ESTADOS:
        return Reply(f"Estado no válido: «{estado}».")
    return confirm_marks([(appid, estado)], backlog_path, sync, refresh)


def _query_rows(store: Store, query: str) -> list[dict]:
    """Todos los juegos que casan con un nombre o saga, en cualquier estado."""
    q = fold(query)
    return ([r for r in store.backlog if q in fold(r["name"])]
            or _token_matches(store.backlog, query) or find_games(store.backlog, query))


def started_pending(store: Store, query: str | None = None) -> list[dict]:
    """Juegos en «pendiente» que ya empezaste, lo jugado hace menos primero.

    Con `query` («sherlock holmes») entran todos los pendientes que casen, empezados o no."""
    if query:
        rows = [r for r in _query_rows(store, query) if r["estado"] == "pendiente"]
    else:
        rows = [r for r in store.backlog if r["estado"] == "pendiente"
                and (store.library.get(r["appid"], {}).get("hours") or 0) >= STARTED_HOURS]

    def key(r):
        lib = store.library.get(r["appid"], {})
        last = lib.get("last_played")
        return (last is not None, last.isoformat() if last else "", lib.get("hours") or 0)
    return sorted(rows, key=key, reverse=True)


REVIEW_BUTTONS = (("Terminado", "t"), ("Jugando", "j"), ("Abandonado", "a"),
                  ("Descartado", "d"), ("Saltar", "s"))
REVIEW_OVER = "Este repaso ya terminó. Empieza otro con /repasar."


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def cmd_repasar(store: Store, skip=frozenset(), done: int = 0, query: str | None = None) -> Reply | None:
    """Siguiente juego del repaso, o None si no queda ninguno."""
    rows = [r for r in started_pending(store, query) if r["appid"] not in skip]
    if not rows:
        return None
    row = rows[0]
    what = f"Repaso de «{query}»" if query else "Repaso de juegos empezados que siguen en pendiente"
    head = f"{what} (quedan {len(rows)}"
    head += f", llevas {done})" if done else ")"
    buttons = [(label, f"rev:{row['appid']}:{code}") for label, code in REVIEW_BUTTONS]
    return Reply(f"{head}\n\n{_detail(store, row, hint=False)}", buttons + [("Parar", "rev:stop")],
                 shown=[(row["appid"], row["name"])], columns=3)


def cmd_estados(store: Store, query: str) -> Reply:
    """Estado actual de los juegos de un nombre o saga (o de varios separados por «;»)."""
    rows = []
    for part in [p for p in re.split(r"[;\n]", query) if p.strip()]:
        rows += [r for r in _query_rows(store, part.strip()) if r not in rows]
    rows = rows[:15]
    if not rows:
        return Reply(f"No encuentro juegos que casen con «{query}».")
    lines = [f"• {r['name']} — {r['estado']}" for r in rows]
    return Reply("\n".join(lines), shown=[(r["appid"], r["name"]) for r in rows])


def start_review(store: Store, state: dict, query: str | None = None) -> Reply:
    query = (query or "").strip() or None
    state.update(review_skip=set(), review_done=[], review_query=query, review_active=True)
    reply = cmd_repasar(store, query=query)
    if reply is not None:
        return reply
    state["review_active"] = False
    if query:
        states = cmd_estados(store, query)
        if not states.shown:
            return states
        return Reply(f"Ya no queda ninguno de «{query}» en «pendiente». Están así:\n{states.text}",
                     shown=states.shown)
    return Reply("No tienes juegos empezados en «pendiente». Todo al día 👌")


def finish_review(state: dict, sync=git_sync.commit_and_push, refresh=None) -> Reply:
    done = state.get("review_done") or []
    state["review_done"], state["review_active"] = [], False
    if not done:
        return Reply("Repaso terminado; no has cambiado nada.")
    text = (f"Repaso terminado: {_plural(len(done), 'juego actualizado', 'juegos actualizados')}\n"
            + "\n".join(f"• {name} → {estado}" for _, name, estado in done))
    suffix = _sync_and_refresh(f"backlog: repaso ({_plural(len(done), 'juego', 'juegos')})", sync, refresh)
    return Reply(text + suffix, shown=[(a, name) for a, name, _ in done])


def review_step(data: str, state: dict, backlog_path=BACKLOG_CSV, sync=git_sync.commit_and_push,
                refresh=None, load=None) -> Reply:
    """Botón del repaso: «rev:<appid>:<código>» marca (sin subir aún) o salta; «rev:stop» acaba.

    Los cambios se suben a GitHub de una vez al terminar, no uno por botón. Un segundo
    toque sobre un juego ya atendido, o sobre un repaso terminado, no hace nada."""
    if not state.get("review_active"):
        return Reply(REVIEW_OVER)
    _, appid, code = (data.split(":", 2) + ["", ""])[:3]
    if appid != "stop":
        done = state.setdefault("review_done", [])
        skip = state.setdefault("review_skip", set())
        if appid not in skip and all(a != appid for a, _, _ in done):
            if code == "s":
                skip.add(appid)
            elif code in CODES and set_estado(appid, CODES[code], backlog_path):
                name = next((r["name"] for r in read_backlog(backlog_path) if r["appid"] == appid), appid)
                done.append((appid, name, CODES[code]))
        nxt = cmd_repasar((load or load_store)(), skip, len(done), state.get("review_query"))
        if nxt is not None:
            return nxt
    return finish_review(state, sync, refresh)


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


def _turns(history: list[dict] | None) -> list:
    return [llm_module.Turn(h["user"], [n for _, n in h.get("shown") or []]) for h in history or []]


def _last_shown(history: list[dict] | None) -> list[tuple[str, str]]:
    return next((list(h["shown"]) for h in reversed(history or []) if h.get("shown")), [])


def free_text(store: Store, text: str, llm=llm_module, history: list[dict] | None = None,
              state: dict | None = None) -> Reply:
    """Texto libre. `history`: turnos anteriores, [{"user": texto, "shown": [(appid, nombre)]}].
    `state`: estado del chat (el repaso guarda ahí su progreso)."""
    args = (store.games, store.scores, store.backlog)
    if not store.scores:
        return Reply(NO_DATA)  # sin datos no merece la pena gastar una llamada
    try:
        request = llm.parse_request(text, top_tags(store.games), history=_turns(history))
    except llm.LLMError as e:
        log.warning("LLM (parse_request) falló: %s", e)
        recs = recommend(Filters(), *args)
        if not recs:
            return Reply(NO_DATA)
        why = ("Este mes ya se ha gastado el presupuesto de IA (/coste)"
               if isinstance(e, llm_module.BudgetExceeded) else "No he podido usar la IA ahora mismo")
        return Reply(f"{why}; aquí van tus recomendaciones de siempre:\n\n" + render(None, recs),
                     shown=_shown(recs))
    if request.kind == "game":
        log.info("Texto libre «%s» → fichas de %s", text, request.games)
        return cmd_juegos(store, request.games, _last_shown(history))
    if request.kind == "status":
        log.info("Texto libre «%s» → estados «%s»", text, request.game)
        found = cmd_estados(store, request.game) if request.game else None
        if found is not None and found.shown:
            return found
        if _last_shown(history):  # «sus estados»: los últimos que se mostraron
            return cmd_mostrados(store, _last_shown(history))
        return found or Reply("¿De qué juegos? Dime un nombre o una saga.")
    if request.kind == "help":
        log.info("Texto libre «%s» → %s", text, request.game)
        return cmd_coste() if request.game == "coste" else cmd_ayuda()
    if request.kind == "review":
        log.info("Texto libre «%s» → repaso «%s»", text, request.game)
        return start_review(store, state if state is not None else {}, request.game)
    if request.kind == "recent":
        log.info("Texto libre «%s» → recientes (%d)", text, request.limit)
        return cmd_recientes(store, request.limit)
    if request.kind == "mark":
        log.info("Texto libre «%s» → marcar %s", text, request.marks)
        return plan_marks(store, request.marks, _last_shown(history))
    if request.kind == "last":
        log.info("Texto libre «%s» → lo mostrado antes", text)
        return cmd_mostrados(store, _last_shown(history))
    filters, note = request.filters, ""
    if request.source:
        wanted = "compartido" if request.source == "familiar" else "propio"
        filters.appids = frozenset(a for a, v in store.library.items() if v.get("source") == wanted)
    if request.game:
        ref = _resolve(store, request.game, _last_shown(history))
        if ref is None:
            note = f"No encuentro «{request.game}» en tu biblioteca; "
        else:
            filters.like_appid = ref["appid"]
    recs = recommend(filters, *args)
    log.info("Texto libre «%s» → %s%s%s → %s", text, filters,
             f" like={request.game!r}" if request.game else "",
             f" origen={request.source}" if request.source else "", [r.name for r in recs])
    if not recs:
        recs = recommend(Filters(limit=filters.limit), *args)
        if not recs:
            return Reply(NO_DATA)
        return Reply(f"{note}No he encontrado nada exacto; esto es lo que más te encaja en general:\n\n"
                     + render(None, recs), shown=_shown(recs))
    try:
        intro = llm.write_intro(text, recs)
    except llm.LLMError as e:
        log.warning("LLM (write_intro) falló: %s", e)
        return Reply(note + render(None, recs), shown=_shown(recs))
    return Reply(f"{note}{intro}\n\n{render(None, recs)}", shown=_shown(recs))
