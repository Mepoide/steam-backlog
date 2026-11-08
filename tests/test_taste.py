import math
from datetime import date, timedelta

import pytest

from conftest import make_backlog, make_game
from library import Playtime
from taste import build_profile, cosine, quality, score_pending, tag_vector

TODAY = date(2026, 11, 7)


def pt(hours, days_ago=None):
    return Playtime(hours, TODAY - timedelta(days=days_ago) if days_ago is not None else None)


def test_tag_vector_normalizes_and_caps():
    assert tag_vector(make_game(tags={"A": 100, "B": 50})) == {"A": 1.0, "B": 0.5}
    many = make_game(tags={f"T{i}": 100 - i for i in range(15)})
    vec = tag_vector(many)
    assert len(vec) == 10 and "T0" in vec and "T14" not in vec


def test_tag_vector_falls_back_to_genres():
    assert tag_vector(make_game(genres=["Acción", "Indie"])) == {"Acción": 1.0, "Indie": 1.0}
    assert tag_vector(make_game()) == {}


def test_profile_log_damping():
    games = {"x": make_game(tags={"A": 10}), "y": make_game(tags={"B": 10})}
    p = build_profile(games, {"x": pt(300), "y": pt(10)}, TODAY)
    assert p["A"] / p["B"] == pytest.approx(math.log1p(300) / math.log1p(10))
    assert math.sqrt(sum(v * v for v in p.values())) == pytest.approx(1.0)


def test_profile_recency_boost():
    games = {"x": make_game(tags={"A": 10}), "y": make_game(tags={"B": 10})}
    p = build_profile(games, {"x": pt(10, days_ago=30), "y": pt(10, days_ago=400)}, TODAY)
    assert p["A"] / p["B"] == pytest.approx(1.5)


def test_unplayed_games_do_not_count():
    games = {"x": make_game(tags={"A": 10}), "z": make_game(tags={"C": 10})}
    p = build_profile(games, {"x": pt(5), "z": pt(0)}, TODAY)
    assert "C" not in p


def test_quality_values():
    assert quality(make_game()) == 0.75
    assert quality(make_game(pos=10, total=10)) == pytest.approx(47.5 / 60)
    assert quality(make_game(pos=10000, total=10000)) > 0.99


def test_cosine_empty_is_zero():
    assert cosine({}, {"A": 1.0}) == 0.0


def scenario():
    games = {
        "x": make_game("Elden Ring", tags={"Souls-like": 100, "Fantasía oscura": 80, "Mundo abierto": 60}),
        "y": make_game("Tetris", tags={"Puzles": 100}),
        "p1": make_game("Lies of P", tags={"Souls-like": 100, "Fantasía oscura": 90}, pos=90, total=100),
        "p2": make_game("Stardew", tags={"Granjas": 100}, pos=90, total=100),
        "p3": make_game("Sin datos"),
    }
    playtime = {"x": pt(120), "y": pt(2)}
    backlog = make_backlog(("x", "Elden Ring", "terminado"), ("y", "Tetris", "jugando"),
                           ("p1", "Lies of P", "pendiente"), ("p2", "Stardew", "pendiente"),
                           ("p3", "Sin datos", "pendiente"))
    return games, playtime, backlog, build_profile(games, playtime, TODAY)


def test_similar_pending_scores_higher():
    s = score_pending(*scenario())
    assert s["p1"]["score"] > s["p2"]["score"]
    assert s["p1"]["score"] == pytest.approx(0.7 * s["p1"]["sim"] + 0.3 * s["p1"]["quality"])


def test_explanation_anchor_and_shared_tags():
    s = score_pending(*scenario())["p1"]
    assert (s["anchor_appid"], s["anchor_name"]) == ("x", "Elden Ring")
    assert s["shared_tags"] == ["Souls-like", "Fantasía oscura"]


def test_pending_without_tags_has_no_anchor():
    s = score_pending(*scenario())["p3"]
    assert s["sim"] == 0.0
    assert (s["anchor_appid"], s["anchor_name"], s["shared_tags"]) == (None, None, [])


def test_only_pendiente_is_scored():
    assert set(score_pending(*scenario())) == {"p1", "p2", "p3"}


def test_pending_game_is_not_its_own_anchor():
    games, playtime, backlog, _ = scenario()
    playtime["p1"] = pt(500)  # empezado pero sigue pendiente
    profile = build_profile(games, playtime, TODAY)
    assert score_pending(games, playtime, backlog, profile)["p1"]["anchor_appid"] == "x"


def test_idf_downweights_ubiquitous_tags():
    from taste import idf_weights
    games = {str(i): make_game(tags={"Acción": 100, f"Raro{i}": 50}) for i in range(10)}
    idf = idf_weights(games)
    assert idf["Acción"] < idf["Raro0"]
    assert idf["Acción"] == pytest.approx(1.0)  # está en todos: peso mínimo, nunca cero


def test_idf_prefers_distinctive_shared_tags():
    # Jugaste mucho a un roguelike de acción. Dos pendientes: uno solo comparte «Acción»
    # (que tiene media biblioteca) con más peso; el otro comparte «Roguelike».
    games = {"played": make_game("Dead Cells", tags={"Acción": 100, "Roguelike": 90})}
    for i in range(8):
        games[f"filler{i}"] = make_game(f"F{i}", tags={"Acción": 100, f"Otro{i}": 50})
    games["shooter"] = make_game("Shooter", tags={"Acción": 100, "Disparos": 40})
    games["rogue"] = make_game("Hades", tags={"Roguelike": 100, "Mitología": 80})
    playtime = {"played": pt(80)}
    backlog = make_backlog(("shooter", "Shooter", "pendiente"), ("rogue", "Hades", "pendiente"))
    from taste import idf_weights
    idf = idf_weights(games)
    profile = build_profile(games, playtime, TODAY, idf=idf)
    s = score_pending(games, playtime, backlog, profile, idf=idf)
    assert s["rogue"]["sim"] > s["shooter"]["sim"]
    assert s["rogue"]["shared_tags"] == ["Roguelike"]
    # Sin IDF (comportamiento anterior) ganaba el shooter: lo que este cambio corrige.
    plain = score_pending(games, playtime, backlog, build_profile(games, playtime, TODAY))
    assert plain["shooter"]["sim"] > plain["rogue"]["sim"]


def test_scores_include_played_hours():
    s = score_pending(*scenario())
    assert s["p1"]["played_hours"] == 0.0
    games, playtime, backlog, _ = scenario()
    playtime["p1"] = pt(3.5)
    assert score_pending(games, playtime, backlog, build_profile(games, playtime, TODAY))["p1"][
        "played_hours"] == 3.5
