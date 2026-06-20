"""Ranking methodology — online Elo + batch Bradley-Terry with bootstrap CIs.

Mirrors Chatbot Arena: Elo gives instant per-vote feedback; Bradley-Terry MLE on
the full pairwise win record is the authoritative leaderboard, with bootstrap
confidence intervals so generators are ranked with uncertainty, not just points.

Pure functions, no DB or framework dependencies, so they are trivially testable.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

# --------------------------------------------------------------------------- Elo


def expected_score(rating_a: float, rating_b: float) -> float:
    """Probability that A beats B under the logistic Elo model."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def elo_update(
    rating_a: float, rating_b: float, score_a: float, k: float = 32.0
) -> tuple[float, float]:
    """Return updated (rating_a, rating_b) after a game.

    score_a is A's realized score: 1.0 win, 0.0 loss, 0.5 tie.
    """
    exp_a = expected_score(rating_a, rating_b)
    delta = k * (score_a - exp_a)
    return rating_a + delta, rating_b - delta


# ------------------------------------------------------------------ Bradley-Terry

# Convert multiplicative BT strengths (geometric-mean-normalized to 1.0) to an
# Elo-like readable scale centered on BASE.
BT_SCALE = 400.0
BT_BASE = 1000.0


@dataclass
class BTResult:
    """Bradley-Terry fit: per-player Elo-scaled scores + bootstrap CIs + game counts."""

    scores: dict[int, float]  # player_id -> Elo-scaled BT score
    lower: dict[int, float]  # 2.5th percentile
    upper: dict[int, float]  # 97.5th percentile
    n_games: dict[int, float]  # games played (ties count as 1)


def _strength_to_elo(strength: dict[int, float]) -> dict[int, float]:
    """Normalize strengths to geometric mean 1, map to Elo-like scale."""
    if not strength:
        return {}
    log_vals = [math.log(s) for s in strength.values()]
    mean_log = sum(log_vals) / len(log_vals)
    return {
        pid: BT_BASE + BT_SCALE / math.log(10.0) * (math.log(s) - mean_log)
        for pid, s in strength.items()
    }


def _fit_strengths(
    players: list[int],
    matches: list[tuple[int, int]],
    reg: float = 0.1,
    max_iter: int = 500,
    tol: float = 1e-9,
) -> dict[int, float]:
    """Bradley-Terry MLE via the MM (minorization-maximization) algorithm.

    matches: list of (winner_id, loser_id). Ties should be excluded by the caller.
    reg: symmetric pseudo-count added to each ordered player pair that played, to
         keep strengths finite when a player has only wins or only losses.
    Returns multiplicative strengths (un-normalized).
    """
    if not players:
        return {}

    wins: dict[int, float] = defaultdict(float)  # total wins per player
    pair_games: dict[tuple[int, int], float] = defaultdict(float)  # unordered pair -> games
    for w, loser in matches:
        wins[w] += 1.0
        key = (min(w, loser), max(w, loser))
        pair_games[key] += 1.0

    # Apply symmetric regularization on pairs that actually played: pretend each
    # such pair also had `reg` wins in each direction. Keeps the MLE well-posed.
    for (i, j), _ in list(pair_games.items()):
        wins[i] += reg
        wins[j] += reg
        pair_games[(i, j)] += 2.0 * reg

    strength = {p: 1.0 for p in players}
    for _ in range(max_iter):
        new_strength: dict[int, float] = {}
        max_change = 0.0
        for p in players:
            denom = 0.0
            for (i, j), n in pair_games.items():
                if i == p:
                    denom += n / (strength[p] + strength[j])
                elif j == p:
                    denom += n / (strength[p] + strength[i])
            if denom <= 0.0 or wins[p] <= 0.0:
                new_strength[p] = strength[p]  # untouched (no games) — stays at prior
            else:
                new_strength[p] = wins[p] / denom
        # Renormalize to geometric mean 1 to prevent drift.
        log_mean = sum(math.log(max(v, 1e-12)) for v in new_strength.values()) / len(new_strength)
        norm = math.exp(log_mean)
        for p in players:
            new_strength[p] /= norm
            max_change = max(max_change, abs(new_strength[p] - strength[p]))
        strength = new_strength
        if max_change < tol:
            break
    return strength


def _bootstrap_scores(
    players: list[int],
    matches: list[tuple[int, int]],
    bootstrap: int,
    reg: float = 0.1,
    seed: int = 12345,
) -> dict[int, list[float]]:
    """Resample the match list `bootstrap` times; return per-player Elo-score samples.

    Index b across players corresponds to the SAME resample, so the samples are
    paired — enabling paired pairwise significance, not just marginal CIs.
    """
    samples: dict[int, list[float]] = defaultdict(list)
    if bootstrap <= 0 or not matches:
        return samples
    rng = random.Random(seed)
    n = len(matches)
    for _ in range(bootstrap):
        resampled = [matches[rng.randrange(n)] for _ in range(n)]
        elo = _strength_to_elo(_fit_strengths(players, resampled, reg=reg))
        for pid, val in elo.items():
            samples[pid].append(val)
    return samples


def bradley_terry(
    players: list[int],
    matches: list[tuple[int, int]],
    bootstrap: int = 200,
    reg: float = 0.1,
    seed: int = 12345,
) -> BTResult:
    """Fit Bradley-Terry and bootstrap a 95% CI on the Elo-scaled scores.

    players: all generator ids that should appear (even with 0 games).
    matches: decisive (winner, loser) pairs; ties/bad excluded by caller.
    """
    point = _strength_to_elo(_fit_strengths(players, matches, reg=reg))

    # Game counts (decisive games only).
    n_games: dict[int, float] = defaultdict(float)
    for w, loser in matches:
        n_games[w] += 1.0
        n_games[loser] += 1.0

    lower: dict[int, float] = dict(point)
    upper: dict[int, float] = dict(point)
    samples = _bootstrap_scores(players, matches, bootstrap, reg=reg, seed=seed)
    for pid in players:
        vals = sorted(samples.get(pid, []))
        if vals:
            lower[pid] = vals[max(0, int(0.025 * len(vals)) - 1)]
            upper[pid] = vals[min(len(vals) - 1, int(0.975 * len(vals)))]

    return BTResult(
        scores=point,
        lower={p: lower.get(p, BT_BASE) for p in players},
        upper={p: upper.get(p, BT_BASE) for p in players},
        n_games={p: n_games.get(p, 0.0) for p in players},
    )


@dataclass
class SignificanceResult:
    """Pairwise superiority probabilities from the paired bootstrap."""

    order: list[int]  # players ranked by point score, best first
    scores: dict[int, float]  # Elo-scaled BT point scores
    p_better: dict[tuple[int, int], float]  # P(score_i > score_j) over resamples
    p_beats_next: dict[int, float | None]  # P(beats the next-ranked player); None for last


def significance_matrix(
    players: list[int],
    matches: list[tuple[int, int]],
    bootstrap: int = 200,
    reg: float = 0.1,
    seed: int = 12345,
) -> SignificanceResult:
    """Bootstrap P(A ranks above B) for every pair — answers "is A *meaningfully* ahead?"."""
    point = _strength_to_elo(_fit_strengths(players, matches, reg=reg))
    order = sorted(players, key=lambda p: point.get(p, BT_BASE), reverse=True)
    samples = _bootstrap_scores(players, matches, bootstrap, reg=reg, seed=seed)

    p_better: dict[tuple[int, int], float] = {}
    for i in players:
        for j in players:
            if i == j:
                continue
            si, sj = samples.get(i, []), samples.get(j, [])
            if not si or not sj or len(si) != len(sj):
                p_better[(i, j)] = 0.5  # no bootstrap data → uninformative
                continue
            wins = sum(1.0 for a, b in zip(si, sj) if a > b)
            wins += 0.5 * sum(1.0 for a, b in zip(si, sj) if a == b)
            p_better[(i, j)] = wins / len(si)

    p_beats_next: dict[int, float | None] = {}
    for idx, p in enumerate(order):
        nxt = order[idx + 1] if idx + 1 < len(order) else None
        p_beats_next[p] = p_better.get((p, nxt), 0.5) if nxt is not None else None

    return SignificanceResult(
        order=order, scores=point, p_better=p_better, p_beats_next=p_beats_next
    )
