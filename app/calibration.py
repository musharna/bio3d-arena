"""Calibration subset sampling for the VLM↔human study.

The sampler picks a stratified set of distinct non-gold pairs (per criterion) that
BOTH the human and the VLM judge vote on the same pairings."""

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
