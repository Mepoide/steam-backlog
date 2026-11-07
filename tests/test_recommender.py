from conftest import make_backlog, make_game
from recommender import ROWS, Filters, Recommendation, netflix_rows, recommend, top_tags


def sc(score, anchor=None, name=None, shared=()):
    return {"score": score, "sim": 0.0, "quality": 0.0, "anchor_appid": anchor,
            "anchor_name": name, "shared_tags": list(shared)}


def pending(*appids):
    return make_backlog(*[(a, a.upper(), "pendiente") for a in appids])


def ids(recs):
    return [r.appid for r in recs]


def test_only_pendiente_recommended():
    games = {"a": make_game("A"), "b": make_game("B")}
    backlog = make_backlog(("a", "A", "pendiente"), ("b", "B", "terminado"))
    assert ids(recommend(Filters(), games, {"a": sc(0.5), "b": sc(0.9)}, backlog)) == ["a"]


def test_max_hours_excludes_longer_and_unknown():
    games = {"a": make_game(hltb_main=3), "b": make_game(hltb_main=20), "c": make_game()}
    scores = {"a": sc(0.1), "b": sc(0.9), "c": sc(0.8)}
    assert ids(recommend(Filters(max_hours=5), games, scores, pending("a", "b", "c"))) == ["a"]


def test_include_tags_accent_and_case_insensitive():
    games = {"g1": make_game(genres=["Estrategia"]), "g2": make_game(tags={"Roguelike de acción": 10}),
             "g3": make_game(tags={"Puzles": 5})}
    scores = {k: sc(0.5) for k in games}
    backlog = pending("g1", "g2", "g3")
    assert ids(recommend(Filters(include_tags=["ESTRATEGIA"]), games, scores, backlog)) == ["g1"]
    assert ids(recommend(Filters(include_tags=["rogue"]), games, scores, backlog)) == ["g2"]
    assert ids(recommend(Filters(include_tags=["accion"]), games, scores, backlog)) == ["g2"]


def test_exclude_tags():
    games = {"a": make_game(tags={"Terror": 10}), "b": make_game(tags={"Puzles": 10})}
    scores = {"a": sc(0.9), "b": sc(0.1)}
    assert ids(recommend(Filters(exclude_tags=["terror"]), games, scores, pending("a", "b"))) == ["b"]


def test_min_positive_and_min_reviews():
    games = {"g1": make_game(pos=95, total=100), "g2": make_game(pos=950, total=1000),
             "g3": make_game(pos=800, total=1000), "g4": make_game()}
    scores = {k: sc(0.5) for k in games}
    f = Filters(min_positive=0.9, min_reviews=500)
    assert ids(recommend(f, games, scores, pending("g1", "g2", "g3", "g4"))) == ["g2"]


def test_variety_skips_repeated_anchor():
    games = {k: make_game(k) for k in ("t1", "t2", "t3")}
    scores = {"t1": sc(0.9, "x", "X", ["A"]), "t2": sc(0.8, "x", "X", ["A"]), "t3": sc(0.7, "y", "Y", ["B"])}
    backlog = pending("t1", "t2", "t3")
    assert ids(recommend(Filters(limit=2), games, scores, backlog)) == ["t1", "t3"]
    assert ids(recommend(Filters(limit=3), games, scores, backlog)) == ["t1", "t3", "t2"]


def test_reason_texts():
    games = {"a": make_game("A"), "b": make_game("B", pos=90, total=100), "c": make_game("C"),
             "d": make_game("D")}
    scores = {"a": sc(0.9, "x", "Elden Ring", ["Souls-like", "Fantasía oscura"]),
              "b": sc(0.8), "c": sc(0.7), "d": sc(0.6, "y", "Tetris", ["Puzles"])}
    recs = {r.appid: r for r in recommend(Filters(limit=4), games, scores, pending("a", "b", "c", "d"))}
    assert recs["a"].reason == "porque jugaste Elden Ring (comparten Souls-like, Fantasía oscura)"
    assert recs["b"].reason == "90 % de reseñas positivas"
    assert recs["c"].reason == "sin datos de reseñas"
    assert recs["d"].reason == "porque jugaste Tetris (comparten Puzles)"
    assert recs["b"].positive_pct == 90 and recs["c"].positive_pct is None


def test_recommendation_fields_and_url():
    games = {"1145360": make_game("Hades", hltb_main=23.5, pos=97, total=100)}
    [r] = recommend(Filters(), games, {"1145360": sc(0.5)}, make_backlog(("1145360", "Hades", "pendiente")))
    assert (r.name, r.hours, r.positive_pct) == ("Hades", 23.5, 97)
    assert r.url == "https://store.steampowered.com/app/1145360"


def test_netflix_rows_no_duplicates_and_skips_empty():
    games = {f"g{i}": make_game(f"G{i}") for i in range(5)}
    games["g5"] = make_game("G5", hltb_main=2)
    games["g6"] = make_game("G6", hltb_main=3, pos=990, total=1000)
    scores = {f"g{i}": sc(1 - i / 10) for i in range(7)}
    rows = netflix_rows(games, scores, pending(*games))
    assert [t for t, _ in rows] == ["Para ti", "Sesión corta (≤ 5 h)"]
    assert ids(rows[0][1]) == ["g0", "g1", "g2", "g3", "g4"]
    assert ids(rows[1][1]) == ["g5", "g6"]
    all_ids = [a for _, recs in rows for a in ids(recs)]
    assert len(all_ids) == len(set(all_ids))
    assert [t for t, _ in ROWS] == ["Para ti", "Sesión corta (≤ 5 h)", "Joyas valoradas (≥ 90 %)"]


def test_no_match_returns_empty_list():
    games = {"a": make_game(tags={"Puzles": 1})}
    assert recommend(Filters(include_tags=["carreras"]), games, {"a": sc(0.5)}, pending("a")) == []


def test_pending_without_score_is_skipped():
    games = {"a": make_game(), "b": make_game()}
    assert ids(recommend(Filters(), games, {"a": sc(0.5)}, pending("a", "b"))) == ["a"]


def test_top_tags_by_frequency():
    games = {"1": make_game(tags={"A": 1, "B": 1}), "2": make_game(tags={"A": 1, "B": 1}),
             "3": make_game(tags={"A": 1, "C": 1})}
    assert top_tags(games, n=2) == ["A", "B"]
