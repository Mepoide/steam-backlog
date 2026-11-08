"""Recomendación sobre el backlog: filtros deterministas + puntuación de taste.py.

Módulo puro: no sabe nada de Telegram ni del LLM (lo reutilizará la web).
"""
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Filters:
    max_hours: float | None = None  # duración total del juego (HLTB, historia principal)
    include_tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    min_positive: float | None = None  # 0-1
    min_reviews: int | None = None
    limit: int = 3


@dataclass
class Recommendation:
    appid: str
    name: str
    score: float
    reason: str
    hours: float | None
    positive_pct: int | None

    @property
    def url(self) -> str:
        return f"https://store.steampowered.com/app/{self.appid}"


ROWS = (
    ("Para ti", Filters(limit=5)),
    ("Sesión corta (≤ 5 h)", Filters(max_hours=5, limit=5)),
    ("Joyas valoradas (≥ 90 %)", Filters(min_positive=0.90, min_reviews=500, limit=5)),
)


def fold(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


MIN_TAG_SHARE = 0.25  # un tag cuenta si pesa al menos esto respecto al tag principal del juego
GENRE_SHARE = 0.5  # los géneros de la tienda no tienen peso: cuentan como un tag medio
RELEVANCE_WEIGHT = 0.5  # con tags pedidos, el orden mezcla gusto (score) y cuánto encaja


def tag_share(entry: dict, term: str) -> float:
    """Cuánto pesa `term` en el juego (0-1, relativo a su tag principal).

    El término casa al principio de palabra: «rogue» → «Roguelike de acción»,
    «terror» → «Supervivencia / Terror», pero «rol» no casa con «Control».
    """
    pattern = re.compile(r"\b" + re.escape(fold(term.strip())))
    tags = entry.get("tags") or {}
    peak = max(tags.values(), default=0) or 1
    best = max((v / peak for k, v in tags.items() if pattern.search(fold(k))), default=0.0)
    if any(pattern.search(fold(g)) for g in entry.get("genres") or []):
        best = max(best, GENRE_SHARE)
    return best


def matches(entry: dict, term: str) -> bool:
    return tag_share(entry, term) >= MIN_TAG_SHARE


def relevance(entry: dict, terms: list[str]) -> float:
    """Media de lo que pesa cada término pedido (0 si no llega al mínimo)."""
    if not terms:
        return 0.0
    shares = [tag_share(entry, t) for t in terms]
    return sum(s for s in shares if s >= MIN_TAG_SHARE) / len(terms)


def _positive(entry: dict) -> float | None:
    total = entry.get("reviews_total")
    return entry["reviews_positive"] / total if total else None


SINGLE_PLAYER = "Un jugador"  # categoría de la tienda (l=spanish)


def _multiplayer_only(entry: dict) -> bool:
    cats = entry.get("categories") or []
    return bool(cats) and SINGLE_PLAYER not in cats


def _passes(f: Filters, entry: dict) -> bool:
    if f.max_hours is not None:
        hours = entry.get("hltb_main")
        if hours is None or hours > f.max_hours:
            return False
        if _multiplayer_only(entry):  # su «duración» en HLTB no significa nada
            return False
    if f.include_tags and not any(matches(entry, t) for t in f.include_tags):
        return False
    if any(matches(entry, t) for t in f.exclude_tags):
        return False
    if f.min_reviews is not None and (entry.get("reviews_total") or 0) < f.min_reviews:
        return False
    if f.min_positive is not None:
        pos = _positive(entry)
        if pos is None or pos < f.min_positive:
            return False
    return True


def _reason(score: dict, pct: int | None) -> str:
    if score.get("anchor_name"):
        shared = score.get("shared_tags") or []
        suffix = f" (comparten {', '.join(shared)})" if shared else ""
        return f"porque jugaste {score['anchor_name']}{suffix}"
    if pct is not None:
        return f"{pct} % de reseñas positivas"
    return "sin datos de reseñas"


def _to_rec(appid: str, entry: dict, score: dict, fallback_name: str) -> Recommendation:
    pos = _positive(entry)
    pct = round(pos * 100) if pos is not None else None
    return Recommendation(appid=appid, name=entry.get("name") or fallback_name,
                          score=score["score"], reason=_reason(score, pct),
                          hours=entry.get("hltb_main"), positive_pct=pct)


def _select(f: Filters, games: dict, scores: dict, backlog: list[dict],
            exclude: set[str] = frozenset()) -> list[Recommendation]:
    candidates = []
    for row in backlog:
        appid = row["appid"]
        if row["estado"] != "pendiente" or appid in exclude or appid not in scores:
            continue
        entry = games.get(appid, {})
        if _passes(f, entry):
            candidates.append((scores[appid], appid, entry, row["name"]))

    def rank(c) -> float:
        if not f.include_tags:
            return c[0]["score"]
        return (1 - RELEVANCE_WEIGHT) * c[0]["score"] + RELEVANCE_WEIGHT * relevance(c[2], f.include_tags)

    candidates.sort(key=rank, reverse=True)

    picked, skipped, anchors = [], [], set()
    for c in candidates:
        anchor = c[0].get("anchor_appid")
        if anchor is not None and anchor in anchors:
            skipped.append(c)
            continue
        if anchor is not None:
            anchors.add(anchor)
        picked.append(c)
        if len(picked) == f.limit:
            break
    picked += skipped[: f.limit - len(picked)]
    return [_to_rec(appid, entry, s, name) for s, appid, entry, name in picked]


def recommend(filters: Filters, games: dict, scores: dict, backlog: list[dict]) -> list[Recommendation]:
    return _select(filters, games, scores, backlog)


def netflix_rows(games: dict, scores: dict, backlog: list[dict]) -> list[tuple[str, list[Recommendation]]]:
    rows, used = [], set()
    for title, f in ROWS:
        recs = _select(f, games, scores, backlog, exclude=used)
        used.update(r.appid for r in recs)
        if recs:
            rows.append((title, recs))
    return rows


def top_tags(games: dict, n: int | None = None) -> list[str]:
    counts = Counter(t for e in games.values() for t in (e.get("tags") or {}))
    return [t for t, _ in counts.most_common(n)]
