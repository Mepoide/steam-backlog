from conftest import make_backlog, make_game
from recommender import ROWS, Filters, Recommendation, netflix_rows, recommend, top_tags


def sc(score, anchor=None, name=None, shared=(), quality=0.0):
    return {"score": score, "sim": 0.0, "quality": quality, "anchor_appid": anchor,
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


def test_minor_tags_do_not_match():
    from recommender import matches
    shooter = make_game(tags={"Disparos": 1373, "Estrategia": 166})
    assert not matches(shooter, "estrategia")
    assert matches(make_game(tags={"Disparos": 1000, "Estrategia": 479}), "estrategia")
    assert matches(make_game(tags={"Disparos": 1000}, genres=["Estrategia"]), "estrategia")


def test_terms_match_at_word_start_only():
    from recommender import matches
    assert not matches(make_game(tags={"Control": 10}), "rol")
    assert matches(make_game(tags={"Rol de acción": 10}), "rol")
    assert matches(make_game(tags={"Supervivencia / Terror": 10}), "terror")
    assert matches(make_game(tags={"Roguelike de acción": 10}), "rogue")


def test_strong_match_beats_higher_score_with_weak_match():
    # «algo relajante tipo granja»: el juego de granja debe ganar al LEGO que solo es algo «Casual».
    games = {"lego": make_game("LEGO", tags={"Acción": 100, "Casuales": 30}),
             "granja": make_game("Granja", tags={"Agricultura": 100, "Relajantes": 90, "Casuales": 60})}
    scores = {"lego": sc(0.8), "granja": sc(0.5)}
    f = Filters(include_tags=["Agricultura", "Relajantes", "Casuales"])
    assert ids(recommend(f, games, scores, pending("lego", "granja"))) == ["granja", "lego"]


def test_without_include_tags_order_is_pure_score():
    games = {"a": make_game(tags={"X": 1}), "b": make_game(tags={"Y": 1})}
    assert ids(recommend(Filters(), games, {"a": sc(0.4), "b": sc(0.6)}, pending("a", "b"))) == ["b", "a"]


def test_top_tags_default_is_all():
    games = {str(i): make_game(tags={f"T{i}": 1}) for i in range(120)}
    assert len(top_tags(games)) == 120


def test_max_hours_skips_multiplayer_only_games():
    # HLTB da «2,5 h» a Half-Life 2: Deathmatch, pero un juego solo multijugador no «se termina».
    mp = make_game("HL2DM", hltb_main=2.5)
    mp["categories"] = ["Multijugador", "JcJ"]
    sp = make_game("Corto", hltb_main=4)
    sp["categories"] = ["Un jugador", "Multijugador"]
    unknown = make_game("Sin categorías", hltb_main=3)  # sin datos de la tienda: se deja pasar
    games = {"mp": mp, "sp": sp, "u": unknown}
    scores = {"mp": sc(0.9), "sp": sc(0.5), "u": sc(0.4)}
    assert ids(recommend(Filters(max_hours=5), games, scores, pending("mp", "sp", "u"))) == ["sp", "u"]
    assert "mp" in ids(recommend(Filters(), games, scores, pending("mp", "sp", "u")))


def test_started_games_are_not_recommended():
    # Las 403 filas del backlog nacen «pendiente»: Civilization VI con 234 h no es una sugerencia.
    games = {"civ": make_game("Civ VI"), "nuevo": make_game("Nuevo"), "probado": make_game("Probado")}
    scores = {"civ": {**sc(0.9), "played_hours": 234.0}, "nuevo": sc(0.5),
              "probado": {**sc(0.7), "played_hours": 1.5}}
    assert ids(recommend(Filters(), games, scores, pending("civ", "nuevo", "probado"))) == ["probado", "nuevo"]


def test_games_matching_all_requested_tags_come_first():
    # «rol por turnos»: el que tiene ambos va antes que el de solo «Rol» aunque puntúe menos.
    games = {"accion": make_game("DBZ", tags={"Rol": 100, "Acción": 100}),
             "turnos": make_game("Divinity", tags={"Rol": 60, "Por turnos": 50})}
    scores = {"accion": sc(0.9), "turnos": sc(0.3)}
    f = Filters(include_tags=["Rol", "Por turnos"])
    assert ids(recommend(f, games, scores, pending("accion", "turnos"))) == ["turnos", "accion"]


def test_like_appid_ranks_by_similarity_and_explains():
    games = {"ref": make_game("Ref", tags={"Rol": 10, "Por turnos": 8}),
             "a": make_game("A", tags={"Rol": 10, "Por turnos": 9}),
             "b": make_game("B", tags={"Deportes": 10, "Por turnos": 5}),
             "c": make_game("C", tags={"Carreras": 10})}
    scores = {"ref": sc(0.1), "a": sc(0.2), "b": sc(0.9, "x", "X"), "c": sc(0.95, "x", "X")}
    recs = recommend(Filters(like_appid="ref", limit=3), games, scores, pending("ref", "a", "b", "c"))
    assert ids(recs) == ["a", "b", "c"]  # «ref» no se recomienda a sí mismo
    assert recs[0].reason == "parecido a Ref (comparten Rol, Por turnos)"


def test_sort_by_rating_and_duration_and_appids():
    games = {"a": make_game(hltb_main=30, pos=99, total=1000), "b": make_game(hltb_main=2, pos=60, total=1000),
             "c": make_game(pos=90, total=1000)}
    scores = {"a": sc(0.1, quality=0.98), "b": sc(0.9, quality=0.6), "c": sc(0.5, quality=0.89)}
    backlog = pending("a", "b", "c")
    assert ids(recommend(Filters(sort="valoracion"), games, scores, backlog)) == ["a", "c", "b"]
    assert ids(recommend(Filters(sort="duracion"), games, scores, backlog)) == ["b", "a", "c"]
    assert ids(recommend(Filters(appids=frozenset({"c"})), games, scores, backlog)) == ["c"]


def test_sort_with_like_only_reorders_the_most_similar():
    from recommender import SORT_POOL
    games = {"ref": make_game("Ref", tags={"Roguelike": 10})}
    scores = {"ref": sc(0.1)}
    for i in range(SORT_POOL):  # parecidos y largos
        games[f"s{i}"] = make_game(tags={"Roguelike": 10}, hltb_main=20 + i)
        scores[f"s{i}"] = sc(0.5)
    games["corto"] = make_game(tags={"Carreras": 10}, hltb_main=0.5)  # muy corto pero nada parecido
    scores["corto"] = sc(0.5)
    recs = recommend(Filters(like_appid="ref", sort="duracion"), games, scores, pending(*games))
    assert ids(recs) == ["s0", "s1", "s2"]


def deck_games():
    return {"v": {**make_game("V"), "deck": 3}, "p": {**make_game("P"), "deck": 2},
            "x": {**make_game("X"), "deck": 1}, "u": {**make_game("U"), "deck": 0},
            "n": make_game("N")}


def test_deck_filter_playable_and_verified():
    games, scores = deck_games(), {k: sc(0.5) for k in "vpxun"}
    backlog = pending(*"vpxun")
    assert set(ids(recommend(Filters(deck="jugable", limit=9), games, scores, backlog))) == {"v", "p"}
    assert ids(recommend(Filters(deck="verificado", limit=9), games, scores, backlog)) == ["v"]
    assert len(recommend(Filters(limit=9), games, scores, backlog)) == 5


def test_recommendation_carries_deck_and_early_access():
    games = {"a": {**make_game("A"), "deck": 2, "early_access": True}}
    [r] = recommend(Filters(), games, {"a": sc(0.5)}, pending("a"))
    assert r.deck == 2 and r.early_access is True


def test_netflix_rows_apply_deck():
    games, scores = deck_games(), {k: sc(0.5) for k in "vpxun"}
    rows = netflix_rows(games, scores, pending(*"vpxun"), deck="verificado")
    assert [a for _, recs in rows for a in ids(recs)] == ["v"]
