"""Tests for the ranking module — Elo + Bradley-Terry."""

from __future__ import annotations

import math

from app import ranking


def test_expected_score_symmetry():
    assert ranking.expected_score(1000, 1000) == 0.5
    a = ranking.expected_score(1200, 1000)
    b = ranking.expected_score(1000, 1200)
    assert math.isclose(a + b, 1.0)
    assert a > 0.5 > b


def test_elo_winner_gains_loser_loses_zero_sum():
    new_a, new_b = ranking.elo_update(1000, 1000, score_a=1.0, k=32)
    assert new_a > 1000 > new_b
    # Zero-sum: total rating conserved.
    assert math.isclose(new_a + new_b, 2000.0)
    # Even match, win → +16, −16.
    assert math.isclose(new_a, 1016.0)


def test_elo_tie_even_match_no_change():
    new_a, new_b = ranking.elo_update(1000, 1000, score_a=0.5, k=32)
    assert math.isclose(new_a, 1000.0)
    assert math.isclose(new_b, 1000.0)


def test_bradley_terry_recovers_known_ordering():
    # Three players with a clear strength order: 1 > 2 > 3.
    players = [1, 2, 3]
    matches = []
    matches += [(1, 2)] * 8 + [(2, 1)] * 2  # 1 beats 2 most of the time
    matches += [(2, 3)] * 8 + [(3, 2)] * 2  # 2 beats 3 most of the time
    matches += [(1, 3)] * 9 + [(3, 1)] * 1  # 1 dominates 3
    result = ranking.bradley_terry(players, matches, bootstrap=50)
    assert result.scores[1] > result.scores[2] > result.scores[3]
    # CIs should bracket the point estimates.
    for p in players:
        assert result.lower[p] <= result.scores[p] <= result.upper[p]
    assert result.n_games[1] == 20  # 10 decisive games vs p2 + 10 vs p3
