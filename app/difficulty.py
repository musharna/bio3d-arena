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


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def tier_scorecard(db) -> list[dict]:
    """Per-(tier × generator) aggregate of the existing objective metrics.

    Tiers in canonical order, then an 'untiered' bucket for tasks with no
    TaskDifficulty row. Means skip missing metric rows (None), never zero-fill.
    """
    from sqlalchemy import select

    from .models import Metric, ModelOutput, OrganMetric
    from .service import generator_display_names

    tier_by_task = {td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()}
    # Disambiguated display names (shared with the Mode-A boards) so the 8 same-named
    # XfrogPlants variants etc. are distinguishable here too.
    gen_name = generator_display_names(db)
    chamfer_by_out = {}
    fscore_by_out = {}
    verdict_by_out = {}
    for m in db.execute(select(Metric)).scalars():
        chamfer_by_out[m.output_id] = m.chamfer
        fscore_by_out[m.output_id] = m.fscore
        verdict_by_out[m.output_id] = m.species_verdict
    structural_by_out = {
        om.output_id: om.botanical_fidelity for om in db.execute(select(OrganMetric)).scalars()
    }

    # acc[(tier, gen_id)] = dict of running lists/counters
    acc: dict[tuple[str, int], dict] = {}
    for out in db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars():
        tier = tier_by_task.get(out.task_id, "untiered")
        key = (tier, out.generator_id)
        a = acc.setdefault(
            key,
            {
                "n_outputs": 0,
                "n_scored": 0,
                "chamfer": [],
                "fscore": [],
                "structural": [],
                "verdicts": [],
            },
        )
        a["n_outputs"] += 1
        scored = out.id in chamfer_by_out
        if scored:
            a["n_scored"] += 1
            if chamfer_by_out[out.id] is not None:
                a["chamfer"].append(chamfer_by_out[out.id])
            if fscore_by_out[out.id] is not None:
                a["fscore"].append(fscore_by_out[out.id])
            if verdict_by_out[out.id] is not None:
                a["verdicts"].append(verdict_by_out[out.id])
        if structural_by_out.get(out.id) is not None:
            a["structural"].append(structural_by_out[out.id])

    out_tiers = list(TIERS) + ["untiered"]
    card = []
    for tier in out_tiers:
        rows = []
        for (t, gid), a in acc.items():
            if t != tier:
                continue
            verdicts = a["verdicts"]
            pass_rate = (
                sum(1 for v in verdicts if v == "PASS") / len(verdicts) if verdicts else None
            )
            rows.append(
                {
                    "generator": gen_name.get(gid, f"#{gid}"),
                    "n_outputs": a["n_outputs"],
                    "n_scored": a["n_scored"],
                    "mean_chamfer": _mean(a["chamfer"]),
                    "mean_fscore": _mean(a["fscore"]),
                    "mean_structural": _mean(a["structural"]),
                    "species_pass_rate": pass_rate,
                }
            )
        rows.sort(key=lambda r: r["generator"])
        card.append({"tier": tier, "rows": rows})
    return card
