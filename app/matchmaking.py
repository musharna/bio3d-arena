"""Pair + task selection for the arena.

Prefers under-sampled outputs (fewest prior comparisons) so each vote buys the
most ranking information, with random tie-breaking and randomized A/B display
order to avoid position bias.
"""

from __future__ import annotations

import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import GoldPair, ModelOutput, Task


def _real_outputs(task: Task) -> list[ModelOutput]:
    """Task outputs eligible for normal matchmaking (gold/decoy assets excluded)."""
    return [o for o in task.outputs if not o.is_gold]


def pick_task(db: Session, category_id: int | None = None) -> Task | None:
    """Pick a random active task that has at least two (non-gold) outputs."""
    stmt = select(Task).where(Task.active.is_(True))
    if category_id is not None:
        stmt = stmt.where(Task.category_id == category_id)
    tasks = db.execute(stmt).scalars().all()
    candidates = [t for t in tasks if len(_real_outputs(t)) >= 2]
    if not candidates:
        return None
    return random.choice(candidates)


def pick_gold_pair(db: Session) -> GoldPair | None:
    """Pick a random gold attention-check pair, if any are configured."""
    golds = db.execute(select(GoldPair)).scalars().all()
    return random.choice(golds) if golds else None


def pick_pair(db: Session, task: Task, exclude_fn=None) -> tuple[ModelOutput, ModelOutput] | None:
    """Pick two distinct (non-gold) outputs for the task, biased toward least-compared.

    `exclude_fn(output) -> bool` may optionally be passed to filter out specific
    outputs (e.g. reference-scan sources) before pair selection.
    """
    outputs = _real_outputs(task)
    if exclude_fn is not None:
        outputs = [o for o in outputs if not exclude_fn(o)]
    if len(outputs) < 2:
        return None
    # Least-sampled first; random tiebreak so equal-count outputs rotate fairly.
    random.shuffle(outputs)
    outputs.sort(key=lambda o: o.n_comparisons)
    a, b = outputs[0], outputs[1]
    # Randomize which side is shown as A vs B to neutralize position bias.
    if random.random() < 0.5:
        a, b = b, a
    return a, b


def total_votes(db: Session) -> int:
    from .models import Vote

    return db.execute(select(func.count(Vote.id))).scalar_one()
