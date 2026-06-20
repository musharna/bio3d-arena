"""Pair + task selection for the arena.

Prefers under-sampled outputs (fewest prior comparisons) so each vote buys the
most ranking information, with random tie-breaking and randomized A/B display
order to avoid position bias.
"""

from __future__ import annotations

import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ModelOutput, Task


def pick_task(db: Session, category_id: int | None = None) -> Task | None:
    """Pick a random active task that has at least two outputs to compare."""
    stmt = select(Task).where(Task.active.is_(True))
    if category_id is not None:
        stmt = stmt.where(Task.category_id == category_id)
    tasks = db.execute(stmt).scalars().all()
    candidates = [t for t in tasks if len(t.outputs) >= 2]
    if not candidates:
        return None
    return random.choice(candidates)


def pick_pair(db: Session, task: Task) -> tuple[ModelOutput, ModelOutput] | None:
    """Pick two distinct outputs for the task, biased toward least-compared ones."""
    outputs = list(task.outputs)
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
