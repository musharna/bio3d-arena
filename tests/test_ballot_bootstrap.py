# tests/test_ballot_bootstrap.py
from app import ranking


def _width(res, pid):
    return res.upper[pid] - res.lower[pid]


def test_ballot_grouping_widens_cis():
    # 20 ballots on player 1 (the hub, present in every ballot). Most ballots have 1
    # sweeping 2/3/4; a minority (indices 0, 5, 10, 15) are a rater who flips ALL 3
    # derived comparisons together (1 loses to everyone that round) -- the correlated,
    # whole-ballot-moves-together structure ballot-grouping exists to capture. Note: 20
    # BYTE-IDENTICAL ballots (no across-ballot outcome variation at all) is a degenerate
    # construction where ballot-level resampling can only ever reproduce the same
    # dataset, so it deterministically yields a NARROWER (zero) CI than per-pair
    # resampling -- the opposite of what this test needs to demonstrate. Genuine
    # between-ballot heterogeneity is required for "resampling 20 ballots, not 60
    # pseudo-independent pairs, cannot be more certain" to hold.
    players = [1, 2, 3, 4]
    ballots = [
        [(2, 1), (3, 1), (4, 1)] if i % 5 == 0 else [(1, 2), (1, 3), (1, 4)] for i in range(20)
    ]
    matches = [m for ballot in ballots for m in ballot]
    groups = [g for g, ballot in enumerate(ballots) for _ in ballot]
    naive = ranking.bradley_terry(players, matches, bootstrap=200)
    grouped = ranking.bradley_terry(players, matches, bootstrap=200, groups=groups)
    # Point estimates identical; grouped CI for the hub player must be strictly wider
    # than the naive per-pair CI (ballot-level resampling of 20 correlated ballots
    # cannot be more certain than pseudo-independent per-pair resampling of 60 pairs).
    assert grouped.scores[1] == naive.scores[1]
    assert _width(grouped, 1) > _width(naive, 1)
