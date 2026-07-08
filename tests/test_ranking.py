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


def _chain(n_players: int, games_per_link: int) -> tuple[list[int], list[tuple[int, int]]]:
    """A transitive dominance chain: player i beats i+1, only neighbors ever play — tie-free and
    near-deterministic, the structure the K-wise same-paradigm VLM-judge board produces."""
    players = list(range(n_players))
    matches = [(i, i + 1) for i in range(n_players - 1) for _ in range(games_per_link)]
    return players, matches


def test_judge_prior_bounds_dominance_chain_and_preserves_order():
    """The evidence-scaled judge prior keeps a tie-free dominance chain within a sane Elo window
    while preserving ordering. Regression for the judge board showing Elo ~ -6400..+28900: an
    unpenalized MLE stretches strengths geometrically along the disconnected/near-separated
    judge graph (a length-20 chain diverges to several thousand)."""
    from app import config

    players, matches = _chain(20, 4)
    result = ranking.bradley_terry(
        players,
        matches,
        bootstrap=0,
        prior_frac=config.JUDGE_PRIOR_FRAC,
        prior_floor=config.JUDGE_PRIOR_FLOOR,
    )
    scores = [result.scores[p] for p in players]

    assert scores == sorted(scores, reverse=True)  # ordering down the chain preserved
    window = 3 * ranking.BT_SCALE  # [-200, 2200] around BT_BASE=1000
    for p, sc in zip(players, scores):
        assert abs(sc - ranking.BT_BASE) <= window, f"player {p} score {sc:.0f} off-scale"


def test_judge_prior_is_volume_robust():
    """The prior scales with each player's game count, so it does NOT wash out at high volume —
    a heavy chain (100 games/link) stays as bounded as a light one. This is exactly the property
    a fixed-count anchor lacked, which is why the real 252-game judge data stayed off-scale until
    the prior was made evidence-proportional."""
    from app import config

    def span(games_per_link: int) -> float:
        players, matches = _chain(16, games_per_link)
        sc = ranking.bradley_terry(
            players,
            matches,
            bootstrap=0,
            prior_frac=config.JUDGE_PRIOR_FRAC,
            prior_floor=config.JUDGE_PRIOR_FLOOR,
        ).scores
        return max(sc.values()) - min(sc.values())

    assert span(100) <= span(4) + ranking.BT_SCALE  # heavy volume ≈ light volume
    assert span(100) <= 4 * ranking.BT_SCALE  # absolute sanity bound


def test_prior_off_by_default_leaves_mle_unchanged():
    """prior_frac/prior_floor default to 0 → the exact unpenalized MLE, so the human pairwise
    board is unaffected. A fit must be byte-for-byte identical with the defaults and with the
    prior explicitly disabled."""
    players = [1, 2, 3]
    matches = (
        [(1, 2)] * 6 + [(2, 1)] * 2 + [(2, 3)] * 6 + [(3, 2)] * 2 + [(1, 3)] * 7 + [(3, 1)] * 1
    )
    default = ranking.bradley_terry(players, matches, bootstrap=0).scores
    prior_off = ranking.bradley_terry(
        players, matches, bootstrap=0, prior_frac=0.0, prior_floor=0.0
    ).scores
    assert default == prior_off


def test_rank_by_ci_groups_overlapping_intervals():
    from app.ranking import rank_by_ci

    # A clearly ahead (CI above all); B and C overlap each other; D clearly last.
    #         A            B            C            D
    bounds = [(1200, 1300), (1000, 1100), (1050, 1150), (800, 900)]
    #  A: nobody's lower > 1300 -> rank 1
    #  B: A's lower(1200) > B.upper(1100) -> 1 beats it -> rank 2
    #  C: A's lower(1200) > C.upper(1150) -> 1 beats it -> rank 2 (ties B; C/B overlap)
    #  D: A,B,C all have lower > D.upper(900) -> 3 beat it -> rank 4
    assert rank_by_ci(bounds) == [1, 2, 2, 4]


def test_rank_by_ci_all_overlap_share_rank_one():
    from app.ranking import rank_by_ci

    assert rank_by_ci([(1000, 1100), (1010, 1110), (990, 1090)]) == [1, 1, 1]


def test_rank_by_ci_empty():
    from app.ranking import rank_by_ci

    assert rank_by_ci([]) == []
