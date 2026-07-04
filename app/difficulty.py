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


def species_slug_for_task(task) -> str:
    """Resolve a task's taxon slug from its title's binomial prefix:
    'Rosa — single-image → 3D reconstruction' → 'rosa',
    'Zea mays — botanical plausibility' → 'zea_mays'. Matches ReconTask.species_slug.
    Fail-loud if the title yields no slug."""
    head = task.title.split("—")[0].strip()
    slug = head.lower().replace(" ", "_")
    if not slug:
        raise ValueError(f"task {task.id} title yields no species slug: {task.title!r}")
    return slug


def materialize_task_difficulty(db, commit: bool = True) -> dict:
    """Project TaxonDifficulty onto per-task TaskDifficulty rows via species_slug_for_task.
    Idempotent (set_task_difficulty upserts by task_id). A task whose resolved species has no
    TaxonDifficulty row is collected into `skipped` (NOT raised) — the seeding script enforces
    fail-loud on a non-empty skipped. commit=False lets tests run under transaction rollback."""
    from .models import Task, TaxonDifficulty

    taxon = {t.species_slug: t for t in db.execute(select(TaxonDifficulty)).scalars()}
    materialized = 0
    skipped: list[tuple[int, str]] = []
    for task in db.execute(select(Task)).scalars():
        slug = species_slug_for_task(task)
        td = taxon.get(slug)
        if td is None:
            skipped.append((task.id, slug))
            continue
        set_task_difficulty(
            db,
            task.id,
            td.tier,
            rationale=f"taxon {slug}: {td.tier} (see TaxonDifficulty)",
            commit=False,
        )
        materialized += 1
    # Explicit flush (SessionLocal is autoflush=False, see app/structural.py:upsert_verdict for
    # the same pattern): makes the rows added above visible to callers querying TaskDifficulty
    # in this same uncommitted session/transaction (tests; a re-run for idempotency), without
    # ending the transaction the way an actual commit would.
    db.flush()
    if commit:
        db.commit()
    return {"materialized": materialized, "skipped": skipped, "taxa": len(taxon)}


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


def paradigm_tier_scorecard(db) -> list[dict]:
    """Per-(tier × paradigm) aggregate of the existing objective metrics — the headline
    cross-paradigm × difficulty grid. Same objective-metric plumbing as tier_scorecard,
    grouped by Generator.paradigm instead of generator. Means skip None (never zero-fill);
    canonical tier order + 'untiered' bucket; empty-paradigm generators bucket under
    'unspecified'. Never recomputes Bradley-Terry; the human path is untouched."""
    from . import paradigms
    from .models import Generator, Metric, ModelOutput, OrganMetric

    tier_by_task = {td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()}
    paradigm_by_gen = {g.id: (g.paradigm or "") for g in db.execute(select(Generator)).scalars()}
    chamfer_by_out, fscore_by_out, verdict_by_out = {}, {}, {}
    for m in db.execute(select(Metric)).scalars():
        chamfer_by_out[m.output_id] = m.chamfer
        fscore_by_out[m.output_id] = m.fscore
        verdict_by_out[m.output_id] = m.species_verdict
    structural_by_out = {
        om.output_id: om.botanical_fidelity for om in db.execute(select(OrganMetric)).scalars()
    }

    acc: dict[tuple[str, str], dict] = {}
    for out in db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars():
        tier = tier_by_task.get(out.task_id, "untiered")
        pgm = paradigm_by_gen.get(out.generator_id, "") or "unspecified"
        a = acc.setdefault(
            (tier, pgm),
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
        if out.id in chamfer_by_out:
            a["n_scored"] += 1
            if chamfer_by_out[out.id] is not None:
                a["chamfer"].append(chamfer_by_out[out.id])
            if fscore_by_out[out.id] is not None:
                a["fscore"].append(fscore_by_out[out.id])
            if verdict_by_out[out.id] is not None:
                a["verdicts"].append(verdict_by_out[out.id])
        if structural_by_out.get(out.id) is not None:
            a["structural"].append(structural_by_out[out.id])

    pgm_order = list(paradigms.PARADIGMS) + ["unspecified"]
    card = []
    for tier in list(TIERS) + ["untiered"]:
        rows = []
        for pgm in pgm_order:
            a = acc.get((tier, pgm))
            if not a:
                continue
            verdicts = a["verdicts"]
            pass_rate = (
                sum(1 for v in verdicts if v == "PASS") / len(verdicts) if verdicts else None
            )
            rows.append(
                {
                    "paradigm": pgm,
                    "paradigm_display": paradigms.DISPLAY_NAMES.get(pgm, pgm),
                    "n_outputs": a["n_outputs"],
                    "n_scored": a["n_scored"],
                    "mean_chamfer": _mean(a["chamfer"]),
                    "mean_fscore": _mean(a["fscore"]),
                    "mean_structural": _mean(a["structural"]),
                    "species_pass_rate": pass_rate,
                }
            )
        card.append({"tier": tier, "rows": rows})
    return card
