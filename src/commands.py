"""Lógica de los comandos del bot, sin Telegram: cada comando devuelve un Reply."""
import difflib
import json
import logging
import re
from dataclasses import dataclass, field

import git_sync
import images
import llm as llm_module
import usage
from config import BACKLOG_CSV, GAMES_JSON, PREFS_JSON, SCORES_JSON
from library import (ESTADOS, atomic_write_json, read_backlog, read_library, read_wishlist,
                     set_estado)
from recommender import (STARTED_HOURS, Filters, Recommendation, fold, netflix_rows, recommend,
                         top_tags)

log = logging.getLogger(__name__)
ESTADOS_TXT = "pendiente, jugando, terminado, abandonado, descartado"
CODES = {"p": "pendiente", "j": "jugando", "t": "terminado", "a": "abandonado", "d": "descartado"}
CODE_OF = {v: k for k, v in CODES.items()}  # en los botones: callback_data ≤ 64 bytes
ESTADO_EMOJI = {"pendiente": "⏳", "jugando": "🎮", "terminado": "✅", "abandonado": "💤",
                "descartado": "🚫"}


def _estado(estado: str) -> str:
    """«✅ terminado»: el estado con su emoji, para leerlo de un vistazo."""
    emoji = ESTADO_EMOJI.get(estado)
    return f"{emoji} {estado}" if emoji else estado


# Categoría de Steam Deck (enrich_sources.DECK_*) → texto de la ficha y marca corta de las listas.
DECK_LABEL = {3: "✅ verificado", 2: "🟡 jugable", 1: "⛔ no compatible", 0: "❔ sin probar"}
DECK_MARK = {3: "Deck ✅", 2: "Deck 🟡", 1: "Deck ⛔"}
DECK_PREF_TEXT = {"jugable": "jugables en Steam Deck (verificados o jugables)",
                  "verificado": "verificados para Steam Deck"}

NO_DATA = "Aún no hay recomendaciones: ejecuta enrich.py y taste.py (o espera al job diario)."
RECOMIENDA_LIMIT = 5


@dataclass
class Reply:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (etiqueta, callback_data)
    shown: list[tuple[str, str]] = field(default_factory=list)  # (appid, nombre) mostrados
    columns: int = 1  # botones por fila
    photos: list[tuple[str, str]] = field(default_factory=list)  # (url, pie) de las imágenes


def _shown(recs: list[Recommendation]) -> list[tuple[str, str]]:
    return [(r.appid, r.name) for r in recs]


MAX_PHOTOS = 10  # lo que cabe en un álbum de Telegram


def _photos(store: "Store", items: list[tuple[str, str]], numbered: bool = True) -> list[tuple[str, str]]:
    """Cabecera de cada juego (si la tienda la tiene), con «1. Nombre» como pie."""
    out = []
    for i, (appid, name) in enumerate(items, 1):
        url = images.image_url(store.games.get(appid) or store.wishlist.get(appid) or {})
        if url:
            out.append((url, f"{i}. {name}" if numbered else name))
    return out[:MAX_PHOTOS]


def _recs_reply(store: "Store", text: str, recs: list[Recommendation]) -> "Reply":
    return Reply(text, shown=_shown(recs), photos=_photos(store, _shown(recs)))


@dataclass
class Store:
    games: dict
    scores: dict
    backlog: list[dict]
    library: dict = field(default_factory=dict)  # export crudo: origen, horas y fechas
    prefs: dict = field(default_factory=dict)  # preferencias guardadas (p. ej. {"deck": "jugable"})
    wishlist: dict = field(default_factory=dict)  # lista de deseos por appid (wishlist.py)

    @property
    def deck(self) -> str | None:
        return self.prefs.get("deck") if self.prefs.get("deck") in DECK_PREF_TEXT else None


def _load_json(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_store() -> Store:
    return Store(games=_load_json(GAMES_JSON), scores=_load_json(SCORES_JSON),
                 backlog=read_backlog(BACKLOG_CSV), library=read_library(),
                 prefs=_load_json(PREFS_JSON), wishlist=read_wishlist())


def save_deck_pref(store: Store, deck: str | None, path=None) -> None:
    """Guarda (o quita, con None) la preferencia de Steam Deck."""
    prefs = dict(store.prefs)
    if deck in DECK_PREF_TEXT:
        prefs["deck"] = deck
    else:
        prefs.pop("deck", None)
    atomic_write_json(path or PREFS_JSON, prefs)
    store.prefs = prefs


def _hours(h: float | None) -> str:
    if h is None:
        return "?"
    return f"{round(h)}" if h >= 10 else f"{h:.1f}"


def render(title: str | None, recs: list[Recommendation]) -> str:
    lines = [title] if title else []
    for i, r in enumerate(recs, 1):
        pct = "?" if r.positive_pct is None else r.positive_pct
        extra = "".join(f" · {m}" for m in (DECK_MARK.get(r.deck),
                                            "acceso anticipado" if r.early_access else None) if m)
        lines += [f"{i}. {r.name} — {_hours(r.hours)} h · {pct} % 👍{extra}", f"   {r.reason}",
                  f"   {r.url}"]
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
        "/deseos — tu lista de deseos, con lo que está por salir y cuándo\n"
        f"/marcar <juego> <estado> — estados: {ESTADOS_TXT}\n"
        "/repasar [nombre] — clasifica con botones los juegos que empezaste y siguen en pendiente "
        "(o solo los de un nombre o saga)\n"
        "/coste — lo que lleva gastado la IA este mes\n\n"
        "O escríbeme sin comando: «algo tranquilo, estoy quemado», «algo como Hades», "
        "«me he pasado el Celeste», «a partir de ahora, solo cosas que vayan en la Steam Deck»."
    )


def cmd_limites() -> Reply:
    return Reply(
        "Eso no lo puedo saber: Steam no comparte qué tienes instalado o descargado (ni en la "
        "Steam Deck ni en el PC), el espacio libre, tus logros, tus amigos ni los precios. "
        "Tampoco veo noticias ni rumores: de un juego que aún no ha salido sé la fecha que "
        "anuncie Steam, no lo que se cuente de él.\n\n"
        "Sí veo tu biblioteca (propia y familiar), tu lista de deseos con sus fechas de salida, "
        "tus horas y fechas, y de cada juego su duración, reseñas, tags, si va en Steam Deck y "
        "si está en acceso anticipado. Prueba «¿el Solasta II va en la Deck?», «¿qué me sale "
        "pronto?» o «algo corto para la Deck»."
    )


def _deck_note(deck: str | None) -> str:
    return f"(solo {DECK_PREF_TEXT[deck]})" if deck in DECK_PREF_TEXT else ""


def cmd_top(store: Store) -> Reply:
    rows = netflix_rows(store.games, store.scores, store.backlog, deck=store.deck)
    if not rows:
        return Reply(NO_DATA)
    shown = [s for _, recs in rows for s in _shown(recs)]
    note = _deck_note(store.deck)
    return Reply((note + "\n\n" if note else "") + "\n\n".join(render(title, recs) for title, recs in rows),
                 shown=shown, photos=_photos(store, shown, numbered=False))


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
    f = parse_recomienda_args(args)
    f.deck = store.deck
    recs = recommend(f, store.games, store.scores, store.backlog)
    if not recs:
        return Reply("Nada encaja con esos filtros. Prueba con más horas o sin género.")
    return _recs_reply(store, render(None, recs), recs)


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
    # Último recurso, para erratas («selestre» → Celeste). Sin artículos ni preposiciones:
    # el «the » compartido hacía que «The Expanse» se pareciera a The Ascent o The Cave.
    q = _significant(query)
    if not q:
        return []
    names = {}
    for r in backlog:
        names.setdefault(_significant(r["name"]), r)
    return [names[k] for k in difflib.get_close_matches(q, list(names), n=n, cutoff=0.6)]


FILLER_WORDS = {"the", "a", "an", "of", "and", "el", "la", "los", "las", "un", "una", "de", "del", "y"}


def _significant(s: str) -> str:
    return " ".join(w for w in _words(s) if w not in FILLER_WORDS)


def _entry_line(store: Store, row: dict) -> str:
    g = store.games.get(row["appid"], {})
    total = g.get("reviews_total")
    pct = f"{round(100 * g['reviews_positive'] / total)}" if total else "?"
    return f"{row['name']} — {_estado(row['estado'])} · {_hours(g.get('hltb_main'))} h · {pct} % 👍"


def cmd_buscar(store: Store, args: str) -> Reply:
    if not args.strip():
        return Reply("Uso: /buscar <texto>")
    rows = find_games(store.backlog, args, n=10)
    if not rows:
        return Reply(f"No encuentro «{args.strip()}» en el backlog.")
    return Reply("\n".join(_entry_line(store, r) for r in rows),
                 shown=[(r["appid"], r["name"]) for r in rows])


def _strict_matches(rows: list[dict], query: str, n: int = 3) -> list[dict]:
    """Exacto primero y luego los que lo contienen, sin búsqueda difusa:
    «hades» → Hades y Hades II."""
    q = fold(query.strip())
    exact = [r for r in rows if fold(r["name"]) == q]
    sub = [r for r in rows if q in fold(r["name"]) and r not in exact]
    return (exact + sub)[:n]


def _game_matches(backlog: list[dict], query: str, n: int = 3) -> list[dict]:
    return _strict_matches(backlog, query, n) or find_games(backlog, query, n=n)


def _played(hours: float | None) -> str:
    return "sin jugar" if not hours else f"{hours:.1f} h"


def _origin(lib: dict) -> str:
    acquired = lib.get("acquired")
    if lib.get("source") == "compartido":
        return "biblioteca familiar" + (f", disponible desde {acquired}" if acquired else "")
    if lib:
        return "comprado" + (f" el {acquired}" if acquired else "")
    return ""


MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre")


def _when(g: dict) -> str:
    """La fecha de salida con la precisión que anunció la editora: el día, el mes, el
    trimestre o el año. Cuando solo se sabe el mes, el día que manda Steam es relleno."""
    date = g.get("release_date")
    if not date:
        return ""
    year, month, _ = date.split("-")
    display = g.get("release_display") or ""
    if display == "date_year":
        return year
    if display == "date_quarter":
        return f"Q{(int(month) - 1) // 3 + 1} de {year}"
    if display == "date_month":
        return f"{MONTHS[int(month) - 1]} de {year}"
    return date


def _release(g: dict) -> str:
    date = g.get("release_date")
    if g.get("coming_soon"):
        # Steam da o un texto ya traducido («Próximamente», «Por confirmarse») o una fecha.
        if note := (g.get("release_note") or "").strip():
            return f"Aún no ha salido: {note}"
        when = _when(g)
        return "Aún no ha salido" + (f" (prevista para {when})" if when else "")
    if g.get("early_access"):
        return "En acceso anticipado" + (f" desde {date}" if date else "")
    return f"Salió el {date}" if date else ""


def _wished(store: Store, appid: str) -> str:
    """«💜 en tu lista de deseos desde el …», si está apuntado."""
    entry = store.wishlist.get(appid)
    if entry is None:
        return ""
    added = entry.get("added")
    return "💜 En tu lista de deseos" + (f" desde el {added}" if added else "")


def _reviews_line(g: dict) -> str:
    total = g.get("reviews_total")
    if not total:
        return ""
    return f"Reseñas: {round(100 * (g.get('reviews_positive') or 0) / total)} % 👍 de {total}"


def _wish_detail(store: Store, appid: str) -> str:
    """Ficha de un juego de la lista de deseos: no está en la biblioteca, así que no
    hay horas ni estado, pero sí fecha de salida."""
    entry = store.wishlist[appid]
    added = entry.get("added")
    lines = [f"{entry.get('name') or appid} — 💜 lista de deseos",
             f"Apuntado el {added}" if added else ""]
    release = _release(entry)
    deck = DECK_LABEL.get(entry.get("deck"))
    lines += [_reviews_line(entry),
              " · ".join(x for x in (release, f"Steam Deck: {deck}" if deck else "") if x),
              "No lo tienes, así que no entra en el backlog ni en las recomendaciones.",
              f"https://store.steampowered.com/app/{appid}"]
    return "\n".join(l for l in lines if l)


def _detail(store: Store, row: dict, hint: bool = True) -> str:
    appid = row["appid"]
    g, lib = store.games.get(appid, {}), store.library.get(appid, {})
    lines = [f"{row['name']} — {_estado(row['estado'])}"]
    hours, last = lib.get("hours"), lib.get("last_played")
    played = (f"{hours:.1f} h jugadas" + (f" (última vez {last})" if last else "")) if hours else "Sin jugar"
    origin = _origin(lib)
    lines.append(played + (f" · {origin}" if origin else ""))
    if g.get("hltb_main"):
        extra = f" · {_hours(g['hltb_extra'])} h con extras" if g.get("hltb_extra") else ""
        lines.append(f"Duración: {_hours(g['hltb_main'])} h historia principal (HowLongToBeat){extra}")
    if reviews := _reviews_line(g):
        lines.append(reviews)
    tags = sorted((g.get("tags") or {}).items(), key=lambda kv: -kv[1])[:6]
    if tags:
        lines.append("Tags: " + ", ".join(t for t, _ in tags))
    release = _release(g)
    deck = DECK_LABEL.get(g.get("deck"))
    if release or deck:
        lines.append(" · ".join(x for x in (release, f"Steam Deck: {deck}" if deck else "") if x))
    # Un juego de la familiar puede seguir apuntado: lo tiene otro, no él.
    if wished := _wished(store, appid):
        lines.append(wished)
    if hint and row["estado"] == "pendiente" and (hours or 0) >= STARTED_HOURS:
        lines.append(f"Sigue en «pendiente» y no te lo recomiendo: /marcar {row['name']} jugando "
                     "(o terminado)")
    lines.append(f"https://store.steampowered.com/app/{appid}")
    return "\n".join(lines)


def _wish_rows(store: Store) -> list[dict]:
    """La lista de deseos con la forma de las filas del backlog, para buscar en ella igual."""
    return [{"appid": appid, "name": e["name"]} for appid, e in store.wishlist.items()
            if e.get("name")]


NOT_FOUND = "No encuentro «{}» en tu biblioteca ni en tu lista de deseos."


def _lookup(store: Store, query: str, n: int = 3) -> tuple[str, list[dict]]:
    """(«backlog» o «deseos», filas) del juego que mejor casa, con dos reglas: lo exacto
    manda sobre lo difuso, y la biblioteca sobre la lista de deseos.

    Así «¿cuándo sale el nuevo de The Expanse?» llega a la lista de deseos aunque la
    búsqueda difusa le vea un parecido a algo del backlog, y «football champions» no
    saca de la lista de deseos un juego que ya tiene con un nombre parecido."""
    wish = _wish_rows(store)
    for kind, rows in (("backlog", _strict_matches(store.backlog, query, n)),
                       ("deseos", _strict_matches(wish, query, n)),
                       ("backlog", find_games(store.backlog, query, n=n)),
                       ("deseos", find_games(wish, query, n=n))):
        if rows:
            return kind, rows
    return "", []


def _card(store: Store, kind: str, row: dict) -> str:
    return _detail(store, row) if kind == "backlog" else _wish_detail(store, row["appid"])


def cmd_juego(store: Store, args: str) -> Reply:
    if not args.strip():
        return Reply("Uso: /juego <nombre>")
    kind, rows = _lookup(store, args)
    if not rows:
        return Reply(NOT_FOUND.format(args.strip()))
    shown = [(r["appid"], r["name"]) for r in rows]
    return Reply("\n\n".join(_card(store, kind, r) for r in rows), shown=shown,
                 photos=_photos(store, shown, numbered=False))


def cmd_juegos(store: Store, names: list[str], shown: list[tuple[str, str]] = ()) -> Reply:
    """Fichas de varios juegos nombrados («awakened, chapter one y devils daughter»)."""
    if len(names) == 1:
        return cmd_juego(store, names[0])
    parts, found, missing = [], [], []
    for name in names:
        # Lo que se acaba de mostrar manda; si no, se busca igual que en /juego.
        row = _near(store, name, list(shown))
        kind, rows = ("backlog", [row]) if row is not None else _lookup(store, name, n=1)
        if not rows:
            missing.append(name)
        elif rows[0]["appid"] not in {appid for appid, _ in found}:
            found.append((rows[0]["appid"], rows[0]["name"]))
            parts.append(_card(store, kind, rows[0]))
    parts += [NOT_FOUND.format(m) for m in missing]
    return Reply("\n\n".join(parts), shown=found, photos=_photos(store, found, numbered=False))


def cmd_parecidos(store: Store, args: str) -> Reply:
    if not args.strip():
        return Reply("Uso: /parecidos <juego>, p. ej. /parecidos Baldur's Gate 3")
    rows = _game_matches(store.backlog, args, n=1)
    if not rows:
        return Reply(f"No encuentro «{args.strip()}» en tu biblioteca.")
    ref = rows[0]
    recs = recommend(Filters(like_appid=ref["appid"], limit=RECOMIENDA_LIMIT, deck=store.deck),
                     store.games, store.scores, store.backlog)
    if not recs:
        return Reply(f"No tengo nada sin empezar que se parezca a {ref['name']}.")
    return _recs_reply(store, render(f"Lo más parecido a {ref['name']} que no has empezado:", recs), recs)


def cmd_mostrados(store: Store, shown: list[tuple[str, str]], header: str | None = None) -> Reply:
    """«¿Esos los tengo?»: origen, duración y horas de lo último que se mostró."""
    if not shown:
        return Reply("Aún no te he enseñado ningún juego en esta conversación.")
    estado = {r["appid"]: r["estado"] for r in store.backlog}
    all_owned = all(a in store.library for a, _ in shown)
    lines = [header or ("Sí, todos están en tu biblioteca:" if all_owned else "Los que te acabo de enseñar:")]
    for i, (appid, name) in enumerate(shown, 1):
        lib, g = store.library.get(appid, {}), store.games.get(appid, {})
        parts = [_origin(lib) or "no lo veo en tu biblioteca"]
        if g.get("hltb_main"):
            parts.append(f"dura {_hours(g['hltb_main'])} h")
        if DECK_MARK.get(g.get("deck")):
            parts.append(DECK_MARK[g["deck"]])
        parts += [_played(lib.get("hours")), _estado(estado.get(appid, "fuera del backlog"))]
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
                     f"{_estado(estado.get(appid, 'fuera del backlog'))}")
    return Reply("\n".join(lines), shown=[(appid, v["name"]) for _, appid, v in dated])


WISHLIST_LIMIT = 15
NO_WISHLIST = ("No tengo tu lista de deseos: ejecuta wishlist.py (o espera al job diario). "
               "Steam solo la comparte si «Detalles de juegos» de tu perfil está en Público.")


def _wish_sort_key(entry: dict) -> tuple:
    """Lo que antes salga primero; lo que no tiene fecha, al final del grupo."""
    date = entry.get("release_date") or ""
    return (not date, date, entry.get("name") or "")


def _when_phrase(entry: dict) -> str:
    """«Próximamente», «sale el 2027-03-15» o «sale en marzo de 2027»."""
    if note := (entry.get("release_note") or "").strip():
        return note
    when = _when(entry)
    if not when:
        return "sin fecha"
    return f"sale el {when}" if when == entry.get("release_date") else f"sale en {when}"


def _wish_line(entry: dict) -> str:
    """Una línea de /deseos: lo que sale pronto con su fecha, lo demás con sus reseñas."""
    parts = []
    if entry.get("coming_soon"):
        parts.append(_when_phrase(entry))
    else:
        total = entry.get("reviews_total")
        parts.append(f"{round(100 * (entry.get('reviews_positive') or 0) / total)} % 👍"
                     if total else "sin reseñas")
    if mark := DECK_MARK.get(entry.get("deck")):
        parts.append(mark)
    if entry.get("early_access"):
        parts.append("acceso anticipado")
    if added := entry.get("added"):
        parts.append(f"apuntado el {added}")
    return f"{entry.get('name') or entry['appid']} — " + " · ".join(parts)


def cmd_deseos(store: Store, limit: int = WISHLIST_LIMIT, upcoming: bool = False) -> Reply:
    """La lista de deseos: primero lo que aún no ha salido, por fecha de salida."""
    if not store.wishlist:
        return Reply(NO_WISHLIST)
    coming = sorted((e for e in store.wishlist.values() if e.get("coming_soon")), key=_wish_sort_key)
    out = sorted((e for e in store.wishlist.values() if not e.get("coming_soon")),
                 key=lambda e: e.get("added") or "", reverse=True)  # lo último apuntado primero
    if upcoming:
        if not coming:
            return Reply(f"De los {len(store.wishlist)} juegos de tu lista de deseos ya han "
                         "salido todos.")
        out = []
    blocks, shown = [], []
    for title, entries in (("Por salir:", coming[:limit]),
                           ("Ya a la venta:", out[:max(limit - len(coming), 0)])):
        if not entries:
            continue
        blocks.append(title + "\n" + "\n".join(f"{i}. {_wish_line(e)}"
                                               for i, e in enumerate(entries, 1)))
        shown += [(e["appid"], e.get("name") or e["appid"]) for e in entries]
    head = f"Tu lista de deseos ({len(store.wishlist)} juegos, {len(coming)} por salir)"
    return Reply(head + "\n\n" + "\n\n".join(blocks), shown=shown,
                 photos=_photos(store, shown, numbered=False))


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


def _near(store: Store, name: str, shown: list[tuple[str, str]]) -> dict | None:
    """Fila del backlog de un juego que el bot acaba de mostrar, si solo casa uno."""
    q = fold(name)
    by_id = {r["appid"]: r for r in store.backlog}
    near = [a for a, n in shown if fold(n) == q] or [a for a, n in shown if q in fold(n)]
    return by_id[near[0]] if len(near) == 1 and near[0] in by_id else None


def _resolve(store: Store, name: str, shown: list[tuple[str, str]]) -> dict | None:
    """Nombre que dio el LLM → fila del backlog, priorizando lo que se acaba de mostrar."""
    row = _near(store, name, shown)
    if row is not None:
        return row
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
            lines.append(f"• {row['name']}: {_estado(row['estado'])} → {_estado(estado)}")
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
        text = "✓ Marcados:\n" + "\n".join(f"• {n} → {_estado(e)}" for n, e in done)
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
    shown = [(row["appid"], row["name"])]
    return Reply(f"{head}\n\n{_detail(store, row, hint=False)}", buttons + [("Parar", "rev:stop")],
                 shown=shown, columns=3, photos=_photos(store, shown, numbered=False))


def cmd_estados(store: Store, query: str) -> Reply:
    """Estado actual de los juegos de un nombre o saga (o de varios separados por «;»)."""
    rows = []
    for part in [p for p in re.split(r"[;\n]", query) if p.strip()]:
        rows += [r for r in _query_rows(store, part.strip()) if r not in rows]
    rows = rows[:15]
    if not rows:
        return Reply(f"No encuentro juegos que casen con «{query}».")
    lines = [f"• {r['name']} — {_estado(r['estado'])}" for r in rows]
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
            + "\n".join(f"• {name} → {_estado(estado)}" for _, name, estado in done))
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
        recs = recommend(Filters(deck=store.deck), *args)
        if not recs:
            return Reply(NO_DATA)
        why = ("Este mes ya se ha gastado el presupuesto de IA (/coste)"
               if isinstance(e, llm_module.BudgetExceeded) else "No he podido usar la IA ahora mismo")
        return _recs_reply(store, f"{why}; aquí van tus recomendaciones de siempre:\n\n"
                           + render(None, recs), recs)
    if request.kind == "game":
        log.info("Texto libre «%s» → fichas de %s", text, request.games)
        return cmd_juegos(store, request.games, _last_shown(history))
    if request.kind == "status":
        log.info("Texto libre «%s» → estados «%s»", text, request.game)
        found = cmd_estados(store, request.game) if request.game else None
        if found is not None and found.shown:
            return found
        if _last_shown(history):  # «sus estados»: los últimos que se mostraron
            return cmd_mostrados(store, _last_shown(history), header="Así están:")
        return found or Reply("¿De qué juegos? Dime un nombre o una saga.")
    if request.kind == "help":
        log.info("Texto libre «%s» → %s", text, request.game)
        return {"coste": cmd_coste, "limites": cmd_limites}.get(request.game, cmd_ayuda)()
    if request.kind == "review":
        log.info("Texto libre «%s» → repaso «%s»", text, request.game)
        return start_review(store, state if state is not None else {}, request.game)
    if request.kind == "recent":
        log.info("Texto libre «%s» → recientes (%d)", text, request.limit)
        return cmd_recientes(store, request.limit)
    if request.kind == "wishlist":
        log.info("Texto libre «%s» → lista de deseos (upcoming=%s)", text, request.upcoming)
        return cmd_deseos(store, upcoming=request.upcoming)
    if request.kind == "mark":
        log.info("Texto libre «%s» → marcar %s", text, request.marks)
        return plan_marks(store, request.marks, _last_shown(history))
    if request.kind == "last":
        log.info("Texto libre «%s» → lo mostrado antes", text)
        return cmd_mostrados(store, _last_shown(history))
    filters, note = request.filters, ""
    # Lo que diga ahora manda; si no menciona la Deck, vale la preferencia guardada.
    filters.deck = None if request.deck == "cualquiera" else (request.deck or store.deck)
    if request.remember:
        save_deck_pref(store, filters.deck)
        note = (f"Apuntado: a partir de ahora solo te recomiendo juegos {DECK_PREF_TEXT[filters.deck]} "
                "(dime «da igual la Deck» para quitarlo).\n\n" if filters.deck else
                "Apuntado: vuelvo a recomendarte sin mirar la Steam Deck.\n\n")
    if request.source:
        wanted = "compartido" if request.source == "familiar" else "propio"
        filters.appids = frozenset(a for a, v in store.library.items() if v.get("source") == wanted)
    if request.game:
        ref = _resolve(store, request.game, _last_shown(history))
        if ref is None:
            note += f"No encuentro «{request.game}» en tu biblioteca; "
        else:
            filters.like_appid = ref["appid"]
    recs = recommend(filters, *args)
    log.info("Texto libre «%s» → %s%s%s%s → %s", text, filters,
             f" like={request.game!r}" if request.game else "",
             f" origen={request.source}" if request.source else "",
             " (guardado)" if request.remember else "", [r.name for r in recs])
    if not recs:
        recs = recommend(Filters(limit=filters.limit, deck=filters.deck), *args)
        if not recs:
            return Reply(NO_DATA)
        return _recs_reply(store, f"{note}No he encontrado nada exacto; esto es lo que más te encaja "
                           "en general:\n\n" + render(None, recs), recs)
    try:
        intro = llm.write_intro(text, recs)
    except llm.LLMError as e:
        log.warning("LLM (write_intro) falló: %s", e)
        return _recs_reply(store, note + render(None, recs), recs)
    return _recs_reply(store, f"{note}{intro}\n\n{render(None, recs)}", recs)
