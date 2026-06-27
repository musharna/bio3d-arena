"""Difficulty-tier dimension: vocabulary, assignment, and the objective scorecard.

Tiers are a manually-curated property of a benchmark Task (TaskDifficulty side table).
The scorecard aggregates the EXISTING objective metrics (Metric, OrganMetric) by
(tier × generator) — it never recomputes Bradley-Terry and never touches the human path.
"""

from __future__ import annotations

from sqlalchemy import select

from .models import Task, TaskDifficulty

TIERS: tuple[str, str, str] = ("easy", "moderate", "hard")
TIER_ORDER: dict[str, int] = {t: i for i, t in enumerate(TIERS)}


def set_task_difficulty(
    db, task_id: int, tier: str, rationale: str = "", commit: bool = True
) -> TaskDifficulty:
    """Assign (or re-assign) a task's difficulty tier. Upserts by task_id."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
    if db.get(Task, task_id) is None:
        raise ValueError(f"no task with id {task_id}")
    row = (
        db.execute(select(TaskDifficulty).where(TaskDifficulty.task_id == task_id))
        .scalars()
        .first()
    )
    if row is None:
        row = TaskDifficulty(task_id=task_id, tier=tier, rationale=rationale)
        db.add(row)
    else:
        row.tier = tier
        row.rationale = rationale
    if commit:
        db.commit()
    return row
