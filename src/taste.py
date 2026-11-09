#!/usr/bin/env python3
"""
Perfil de gusto (la parte "tipo Netflix"): cruza horas jugadas con los tags de
cada juego y puntúa los pendientes del backlog, con su motivo
("porque jugaste X"). Determinista, sin LLM.

    python src/taste.py   → data/enriched/profile.json y scores.json

(No se llama profile.py para no tapar el módulo `profile` de la stdlib.)
"""
import json
import math
import sys
from datetime import date, timedelta

from config import GAMES_JSON, PROFILE_JSON, RAW_CSV, SCORES_JSON
from library import Playtime, atomic_write_json, load_playtime, read_backlog

TOP_TAGS = 10
RECENT_DAYS = 180
RECENT_BOOST = 1.5
PRIOR_POSITIVE = 0.75
PRIOR_WEIGHT = 50
W_SIM, W_QUALITY = 0.7, 0.3
# Lo que dices de un juego pesa en tu perfil: lo terminado cuenta más que unas horas
# sueltas, y lo que dejaste o descartaste resta (no es «porque jugaste X»).
ESTADO_WEIGHT = {"terminado": 1.5, "jugando": 1.2, "abandonado": -0.5, "descartado": -0.5}


def _raw_vector(entry: dict, top_n: int = TOP_TAGS) -> dict[str, float]:
    tags = entry.get("tags") or {}
    if not tags:
        return {g: 1.0 for g in entry.get("genres") or []}
    top = sorted(tags.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    peak = top[0][1] or 1
    return {t: v / peak for t, v in top}


def idf_weights(games: dict) -> dict[str, float]:
    """IDF suavizado: ln((1+N)/(1+df)) + 1.

    Un tag que tiene media biblioteca («Acción», «Multijugador») apenas distingue
    gustos; uno raro («Roguelike», «Agricultura») sí. El +1 deja el mínimo en 1, así
    que los tags comunes pesan menos pero nunca desaparecen.
    """
    vectors = [_raw_vector(e) for e in games.values()]
    vectors = [v for v in vectors if v]
    df: dict[str, int] = {}
    for v in vectors:
        for t in v:
            df[t] = df.get(t, 0) + 1
    n = len(vectors)
    return {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}


def tag_vector(entry: dict, top_n: int = TOP_TAGS, idf: dict[str, float] | None = None) -> dict[str, float]:
    vec = _raw_vector(entry, top_n)
    if idf is None:
        return vec
    return {t: w * idf.get(t, 1.0) for t, w in vec.items()}


def cosine(a: dict, b: dict) -> float:
    dot = sum(v * b[k] for k, v in a.items() if k in b)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _weight(p: Playtime, today: date, estado: str | None = None) -> float:
    recent = p.last_played is not None and p.last_played >= today - timedelta(days=RECENT_DAYS)
    return math.log1p(p.hours) * (RECENT_BOOST if recent else 1.0) * ESTADO_WEIGHT.get(estado, 1.0)


def build_profile(games: dict, playtime: dict[str, Playtime], today: date,
                  idf: dict[str, float] | None = None,
                  estados: dict[str, str] | None = None) -> dict[str, float]:
    estados = estados or {}
    profile: dict[str, float] = {}
    for appid, p in playtime.items():
        if p.hours <= 0 or appid not in games:
            continue
        w = _weight(p, today, estados.get(appid))
        for tag, tw in tag_vector(games[appid], idf=idf).items():
            profile[tag] = profile.get(tag, 0.0) + w * tw
    norm = math.sqrt(sum(v * v for v in profile.values()))
    return {k: v / norm for k, v in profile.items()} if norm else {}


def quality(entry: dict) -> float:
    pos, total = entry.get("reviews_positive"), entry.get("reviews_total")
    if not total:
        return PRIOR_POSITIVE
    return (pos + PRIOR_POSITIVE * PRIOR_WEIGHT) / (total + PRIOR_WEIGHT)


def score_pending(games: dict, playtime: dict[str, Playtime], backlog: list[dict],
                  profile: dict[str, float], idf: dict[str, float] | None = None) -> dict[str, dict]:
    disliked = {r["appid"] for r in backlog if ESTADO_WEIGHT.get(r["estado"], 1.0) < 0}
    played = [(a, tag_vector(games[a], idf=idf), math.log1p(p.hours))
              for a, p in playtime.items() if p.hours > 0 and a in games and a not in disliked]
    scores = {}
    for row in backlog:
        appid = row["appid"]
        if row["estado"] != "pendiente":
            continue
        entry = games.get(appid, {})
        vec = tag_vector(entry, idf=idf)
        sim, q = cosine(profile, vec), quality(entry)

        anchor, best = None, 0.0
        for a, avec, w in played:
            if a == appid:
                continue
            s = cosine(avec, vec) * w
            if s > best:
                anchor, best = a, s
        shared = []
        if anchor:
            avec = tag_vector(games[anchor], idf=idf)
            common = sorted((t for t in vec if t in avec), key=lambda t: vec[t] * avec[t], reverse=True)
            shared = common[:2]
        scores[appid] = {
            "score": W_SIM * sim + W_QUALITY * q, "sim": sim, "quality": q,
            "anchor_appid": anchor, "anchor_name": games[anchor]["name"] if anchor else None,
            "shared_tags": shared,
            "played_hours": playtime[appid].hours if appid in playtime else 0.0,
        }
    return scores


def refresh() -> tuple[int, int]:
    """Recalcula perfil y puntuaciones (job diario y tras marcar juegos en el bot)."""
    for path in (RAW_CSV, GAMES_JSON):
        if not path.exists():
            raise FileNotFoundError(f"No existe {path}. Ejecuta antes export.py y enrich.py.")
    games = json.loads(GAMES_JSON.read_text(encoding="utf-8"))
    playtime = load_playtime()
    backlog = read_backlog()
    idf = idf_weights(games)
    estados = {r["appid"]: r["estado"] for r in backlog}
    profile = build_profile(games, playtime, date.today(), idf=idf, estados=estados)
    scores = score_pending(games, playtime, backlog, profile, idf=idf)
    atomic_write_json(PROFILE_JSON, dict(sorted(profile.items(), key=lambda kv: -kv[1])))
    atomic_write_json(SCORES_JSON, scores)
    return len(profile), len(scores)


def main() -> None:
    try:
        tags, scored = refresh()
    except FileNotFoundError as e:
        sys.exit(str(e))
    print(f"✓ perfil con {tags} tags, {scored} pendientes puntuados")


if __name__ == "__main__":
    main()
