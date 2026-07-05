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


def _scorecard_lookups(db) -> dict:
    """Per-output metric lookups shared by both difficulty scorecards. Only status='ok' Metric
    rows count — error rows (e.g. a GT-less taxon) carry a null chamfer and are failed attempts,
    not scores. Completeness is reference-free, so it populates the cells the objective metrics
    leave null (all fungi)."""
    from .models import Completeness, Metric, OrganMetric

    chamfer, fscore, verdict = {}, {}, {}
    for m in db.execute(select(Metric)).scalars():
        if m.status != "ok":
            continue
        chamfer[m.output_id] = m.chamfer
        fscore[m.output_id] = m.fscore
        verdict[m.output_id] = m.species_verdict
    return {
        "tier_by_task": {
            td.task_id: td.tier for td in db.execute(select(TaskDifficulty)).scalars()
        },
        "chamfer": chamfer,
        "fscore": fscore,
        "verdict": verdict,
        "structural": {
            om.output_id: om.botanical_fidelity for om in db.execute(select(OrganMetric)).scalars()
        },
        "completeness": {
            c.output_id: (c.category, c.score) for c in db.execute(select(Completeness)).scalars()
        },
    }


def _accumulate_scorecard(db, lookups, group_of) -> dict:
    """Bucket every non-gold output into acc[(tier, group)] where group = group_of(output).
    Reads only cached objective/completeness metrics — never the human Bradley-Terry path."""
    from .models import ModelOutput

    lk = lookups
    acc: dict[tuple[str, object], dict] = {}
    for out in db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars():
        tier = lk["tier_by_task"].get(out.task_id, "untiered")
        a = acc.setdefault(
            (tier, group_of(out)),
            {
                "n_outputs": 0,
                "n_scored": 0,
                "chamfer": [],
                "fscore": [],
                "structural": [],
                "verdicts": [],
                "completeness": [],
                "complete_cats": [],
            },
        )
        a["n_outputs"] += 1
        if out.id in lk["chamfer"]:
            a["n_scored"] += 1
            if lk["chamfer"][out.id] is not None:
                a["chamfer"].append(lk["chamfer"][out.id])
            if lk["fscore"][out.id] is not None:
                a["fscore"].append(lk["fscore"][out.id])
            if lk["verdict"][out.id] is not None:
                a["verdicts"].append(lk["verdict"][out.id])
        if lk["structural"].get(out.id) is not None:
            a["structural"].append(lk["structural"][out.id])
        if out.id in lk["completeness"]:
            cat, score = lk["completeness"][out.id]
            a["complete_cats"].append(cat)
            if score is not None:
                a["completeness"].append(score)
    return acc


def _row_stats(a: dict) -> dict:
    """Shared per-row aggregate stats. Means skip None (never zero-fill); pct_complete /
    completeness_n count every output with a Completeness row (any category)."""
    verdicts = a["verdicts"]
    cats = a["complete_cats"]
    return {
        "n_outputs": a["n_outputs"],
        "n_scored": a["n_scored"],
        "mean_chamfer": _mean(a["chamfer"]),
        "mean_fscore": _mean(a["fscore"]),
        "mean_structural": _mean(a["structural"]),
        "species_pass_rate": (
            sum(1 for v in verdicts if v == "PASS") / len(verdicts) if verdicts else None
        ),
        "completeness_n": len(cats),
        "mean_completeness": _mean(a["completeness"]),
        "pct_complete": (sum(1 for c in cats if c == "complete") / len(cats) if cats else None),
    }


def tier_scorecard(db) -> list[dict]:
    """Per-(tier × generator) aggregate of the objective metrics + reference-free completeness.

    Tiers in canonical order, then an 'untiered' bucket for tasks with no TaskDifficulty row.
    Means skip missing rows (None), never zero-fill.
    """
    from .service import generator_display_names

    # Disambiguated display names (shared with the Mode-A boards) so the 8 same-named
    # XfrogPlants variants etc. are distinguishable here too.
    gen_name = generator_display_names(db)
    acc = _accumulate_scorecard(db, _scorecard_lookups(db), lambda out: out.generator_id)
    card = []
    for tier in list(TIERS) + ["untiered"]:
        rows = [
            {"generator": gen_name.get(gid, f"#{gid}"), **_row_stats(a)}
            for (t, gid), a in acc.items()
            if t == tier
        ]
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
    from .models import Generator

    paradigm_by_gen = {g.id: (g.paradigm or "") for g in db.execute(select(Generator)).scalars()}
    acc = _accumulate_scorecard(
        db,
        _scorecard_lookups(db),
        lambda out: paradigm_by_gen.get(out.generator_id, "") or "unspecified",
    )
    pgm_order = list(paradigms.PARADIGMS) + ["unspecified"]
    card = []
    for tier in list(TIERS) + ["untiered"]:
        rows = []
        for pgm in pgm_order:
            a = acc.get((tier, pgm))
            if not a:
                continue
            rows.append(
                {
                    "paradigm": pgm,
                    "paradigm_display": paradigms.DISPLAY_NAMES.get(pgm, pgm),
                    **_row_stats(a),
                }
            )
        card.append({"tier": tier, "rows": rows})
    return card
