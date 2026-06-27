"""Calibration subset sampling and analysis for the VLM↔human study.

The sampler picks a stratified set of distinct non-gold pairs (per criterion) that
BOTH the human and the VLM judge vote on the same pairings.

The analysis functions compute Cohen's κ, self-consistency flip-rate, and
Spearman rank correlation between human and VLM BT scores."""

from __future__ import annotations

import itertools
import random

from sqlalchemy import select

from .matchmaking import _real_outputs
from .models import CalibrationPair, Criterion, Task

STUDY_CRITERIA = ["overall", "visual_quality", "structural_accuracy"]


def _all_pairs_by_task(db) -> list[tuple[int, int, int]]:
    """Every distinct (task_id, lo_output_id, hi_output_id) over active tasks."""
    pairs: list[tuple[int, int, int]] = []
    tasks = db.execute(select(Task).where(Task.active.is_(True))).scalars().all()
    for task in tasks:
        outs = sorted(o.id for o in _real_outputs(task))
        for a, b in itertools.combinations(outs, 2):
            pairs.append((task.id, a, b))
    return pairs


def build_calibration_set(
    db,
    n_per_criterion: int = 50,
    criteria_slugs: list[str] | None = None,
    seed: int = 12345,
    replace: bool = True,
) -> dict:
    """Insert a stratified CalibrationPair sample. Deterministic for a given seed."""
    if criteria_slugs is None:  # [] means "select nothing"; only None means defaults
        criteria_slugs = STUDY_CRITERIA
    if replace:
        db.query(CalibrationPair).delete()
        db.flush()

    universe = _all_pairs_by_task(db)
    rng = random.Random(seed)
    rng.shuffle(universe)

    per: dict[str, int] = {}
    created = 0
    for slug in criteria_slugs:
        crit = db.execute(select(Criterion).where(Criterion.slug == slug)).scalars().first()
        if crit is None:
            per[slug] = 0
            continue
        # By design every criterion draws the SAME pair slice (differentiated only by
        # criterion_id) — a calibration study judges identical pairs under each criterion.
        chosen = universe[:n_per_criterion]
        for task_id, a, b in chosen:
            db.add(
                CalibrationPair(task_id=task_id, output_a_id=a, output_b_id=b, criterion_id=crit.id)
            )
        per[slug] = len(chosen)
        created += len(chosen)
    db.commit()
    return {"created": created, "per_criterion": per}


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------


def cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    """Unweighted Cohen's κ over paired categorical labels. None if no data."""
    n = len(labels_a)
    if n == 0 or n != len(labels_b):
        return None
    cats = sorted(set(labels_a) | set(labels_b))
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    obs = sum(1 for x, y in zip(labels_a, labels_b) if x == y) / n
    ra = [0.0] * k
    rb = [0.0] * k
    for x, y in zip(labels_a, labels_b):
        ra[idx[x]] += 1
        rb[idx[y]] += 1
    exp = sum((ra[i] / n) * (rb[i] / n) for i in range(k))
    if exp >= 1.0:
        return 1.0  # degenerate single-category perfect agreement
    return (obs - exp) / (1.0 - exp)


def canonical_label(winner: str, out_a_id: int, out_b_id: int) -> str:
    """Map a slot vote to an order-independent label vs (lower_id, higher_id)."""
    if winner == "tie":
        return "tie"
    if winner == "bad":
        return "bad"
    lo = min(out_a_id, out_b_id)
    winner_id = out_a_id if winner == "a" else out_b_id
    return "first" if winner_id == lo else "second"


# ---------------------------------------------------------------------------
# DB-backed aggregation functions
# ---------------------------------------------------------------------------


def _human_label_for_pair(db, cp) -> str | None:
    """Latest human canonical label for a CalibrationPair (any session), or None."""
    from .models import Comparison, Vote  # local import avoids cycle

    rows = db.execute(
        select(Vote, Comparison)
        .join(Comparison, Vote.comparison_id == Comparison.id)
        .where(
            Comparison.criterion_id == cp.criterion_id,
            Comparison.is_gold.is_(False),
        )
        .order_by(Vote.id.desc())
    ).all()
    cp_set = {cp.output_a_id, cp.output_b_id}
    for vote, comp in rows:
        if {comp.output_a_id, comp.output_b_id} == cp_set:
            return canonical_label(vote.winner, comp.output_a_id, comp.output_b_id)
    return None


def human_vs_judge_kappa(db, criterion_id: int, view_condition: str) -> dict:
    """Align human Vote and canonical-order JudgeVote on CalibrationPairs; exclude bad."""
    from .models import JudgeVote

    pairs = (
        db.execute(select(CalibrationPair).where(CalibrationPair.criterion_id == criterion_id))
        .scalars()
        .all()
    )
    h_labels: list[str] = []
    j_labels: list[str] = []
    for cp in pairs:
        h = _human_label_for_pair(db, cp)
        lo, hi = sorted((cp.output_a_id, cp.output_b_id))
        jv = (
            db.execute(
                select(JudgeVote).where(
                    JudgeVote.criterion_id == criterion_id,
                    JudgeVote.view_condition == view_condition,
                    JudgeVote.output_a_id == lo,
                    JudgeVote.output_b_id == hi,
                )
            )
            .scalars()
            .first()
        )
        if h is None or jv is None:
            continue
        j = canonical_label(jv.winner, jv.output_a_id, jv.output_b_id)
        if h == "bad" or j == "bad":
            continue
        h_labels.append(h)
        j_labels.append(j)
    return {"kappa": cohens_kappa(h_labels, j_labels), "n": len(h_labels)}


def judge_self_consistency(db, criterion_id: int, view_condition: str) -> dict:
    """Swap-group order-disagreement rate."""
    from .models import JudgeVote

    votes = (
        db.execute(
            select(JudgeVote).where(
                JudgeVote.criterion_id == criterion_id,
                JudgeVote.view_condition == view_condition,
            )
        )
        .scalars()
        .all()
    )
    by_group: dict[str, list] = {}
    for v in votes:
        by_group.setdefault(v.swap_group, []).append(v)
    flips = groups = 0
    for grp in by_group.values():
        if len(grp) != 2:
            continue
        groups += 1
        labels = {canonical_label(v.winner, v.output_a_id, v.output_b_id) for v in grp}
        # A flip = the two canonical labels differ.
        if len(labels) > 1:
            flips += 1
    return {"flip_rate": (flips / groups if groups else None), "n_groups": groups}


def rank_correlation(db, criterion_id: int, view_condition: str) -> dict:
    """Spearman between human Rating.bt_score and JudgeRating.bt_score for shared generators."""
    from scipy.stats import spearmanr

    from .models import JudgeRating, Rating

    human = {
        r.generator_id: r.bt_score
        for r in db.execute(
            select(Rating).where(
                Rating.criterion_id == criterion_id,
                Rating.category_id.is_(None),
            )
        ).scalars()
    }
    vlm = {
        r.generator_id: r.bt_score
        for r in db.execute(
            select(JudgeRating).where(
                JudgeRating.criterion_id == criterion_id,
                JudgeRating.view_condition == view_condition,
                JudgeRating.category_id.is_(None),
            )
        ).scalars()
    }
    shared = sorted(set(human) & set(vlm))
    if len(shared) < 3:
        return {"spearman": None, "n": len(shared)}
    rho, _p = spearmanr([human[g] for g in shared], [vlm[g] for g in shared])
    return {"spearman": float(rho), "n": len(shared)}
