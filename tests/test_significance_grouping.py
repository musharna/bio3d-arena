# tests/test_significance_grouping.py
from app import ranking


def test_significance_matrix_accepts_groups_and_is_not_more_extreme():
    # Same fixture shape as test_ballot_bootstrap.test_ballot_grouping_widens_cis: 20 ballots
    # on hub player 1, most ballots sweeping 2/3/4, a minority (indices 0, 5, 10, 15) flip all
    # 3 derived comparisons together. Ballot-level resampling of the correlated K-wise-derived
    # pairs must not make significance MORE confident than naive per-pair resampling.
    players = [1, 2, 3, 4]
    ballots = [
        [(2, 1), (3, 1), (4, 1)] if i % 5 == 0 else [(1, 2), (1, 3), (1, 4)] for i in range(20)
    ]
    matches = [m for ballot in ballots for m in ballot]
    groups = [g for g, ballot in enumerate(ballots) for _ in ballot]

    naive = ranking.significance_matrix(players, matches, bootstrap=200)
    grouped = ranking.significance_matrix(players, matches, bootstrap=200, groups=groups)

    # groups= runs and returns a valid probability map for every ordered pair.
    for i in players:
        for j in players:
            if i == j:
                continue
            assert 0.0 <= grouped.p_better[(i, j)] <= 1.0

    # Point scores (order) are identical -- grouping only affects the bootstrap distribution.
    assert grouped.order == naive.order
    assert grouped.scores == naive.scores

    # Grouped probabilities must not be MORE extreme (further from 0.5) than the naive
    # per-pair ones for the hub player's comparisons -- ballot correlation should not
    # manufacture extra confidence.
    hub = 1
    for other in (2, 3, 4):
        naive_p = naive.p_better[(hub, other)]
        grouped_p = grouped.p_better[(hub, other)]
        assert abs(grouped_p - 0.5) <= abs(naive_p - 0.5) + 1e-9, (
            f"grouped P(better) for ({hub},{other}) is more extreme than naive: "
            f"grouped={grouped_p} naive={naive_p}"
        )
