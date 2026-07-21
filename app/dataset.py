"""Content helpers for a dataset release (SP3-thin).

Pure builders for the release's preference records + LICENSE + DATASHEET text. No filesystem,
no tarball (that's scripts/build_dataset_release.py). The benchmark bundle itself comes from
scripts/export_public.py.
"""

from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    ReconTask,
    Task,
    TaskDifficulty,
    Vote,
)


def build_preference_records(
    db: Session, comparison_ids: set[int] | None = None, kingdom: str | None = None
) -> dict:
    """Every decided comparison with full provenance — the /api/export.json payload.

    When `comparison_ids` is given, only comparisons in that set are included (used by
    scripts/build_dataset_release.py to scope the release's votes to the release bundle's
    own allowlisted tasks/generators). Default `None` keeps the full unfiltered global log,
    which is what the public /api/export.json route ships.

    `kingdom` (optional, e.g. "fungi") restricts to comparisons whose task's category maps to
    that kingdom via `kingdoms.KINGDOM_OF`; `None`/"all" keeps every kingdom.
    """
    from .kingdoms import KINGDOM_OF, normalize_kingdom

    kingdom = normalize_kingdom(kingdom)
    rows = db.execute(
        select(Vote, Comparison).join(Comparison, Vote.comparison_id == Comparison.id)
    ).all()
    records = []
    for vote, comp in rows:
        if comparison_ids is not None and comp.id not in comparison_ids:
            continue
        task = db.get(Task, comp.task_id)
        if kingdom != "all" and KINGDOM_OF.get(task.category.slug) != kingdom:
            continue
        out_a = db.get(ModelOutput, comp.output_a_id)
        out_b = db.get(ModelOutput, comp.output_b_id)
        crit = db.get(Criterion, comp.criterion_id)
        records.append(
            {
                "comparison_id": comp.id,
                "task": task.title,
                "category": task.category.slug,
                "criterion": crit.slug,
                "generator_a": db.get(Generator, out_a.generator_id).slug,
                "generator_b": db.get(Generator, out_b.generator_id).slug,
                "asset_a": out_a.asset_path,
                "asset_b": out_b.asset_path,
                "winner": vote.winner,
                "session": vote.session_id,
                "voted_at": vote.created.isoformat(),
            }
        )
    return {"n_votes": len(records), "votes": records}


def license_rollup(output_rows: list[dict]) -> list[dict]:
    """Distinct (license, attribution, source) over a bundle's model_output row dicts."""
    seen = set()
    out = []
    for r in output_rows:
        key = (r.get("license") or "", r.get("attribution") or "", r.get("source") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append({"license": key[0], "attribution": key[1], "source": key[2]})
    out.sort(key=lambda d: (d["license"], d["attribution"], d["source"]))
    return out


def render_license(rollup: list[dict]) -> str:
    """Render the bundle's LICENSE text.

    The CC-BY-4.0 blanket applies to assets OUR pipeline produced, so both the declaration
    and the per-asset fallback are driven by public_export.is_own_output — the same predicate
    the export gate uses. Naming one source literal here (it used to say only
    `source=bio3d-arena`) silently mis-declared our commissioned/agentic/procedural assets,
    and the unconditional fallback claimed CC-BY-4.0 over any third-party asset that merely
    arrived without a license label."""
    from .public_export import is_own_output

    lines = [
        "Bio 3D Arena — Benchmark Dataset License",
        "",
        "Each 3D asset retains its original license and attribution, listed below. Assets",
        "authored by Bio 3D Arena's own generation pipeline (source=bio3d-arena, commissioned,",
        "agentic:<model>, procedural:<generator>) are released CC-BY-4.0. Redistribution",
        "of any asset is bound by its stated license.",
        "",
        "Per-asset provenance (license | attribution | source):",
    ]
    for r in rollup:
        if r["license"]:
            label = r["license"]
        elif is_own_output(r["source"]):
            label = "(bio3d-arena CC-BY-4.0)"
        else:
            # Never assert a license we were not granted: an unlabeled third-party asset is
            # unknown, not ours. The export gate should have blocked it long before here.
            label = "(UNKNOWN — not cleared for redistribution)"
        lines.append(f"- {label} | {r['attribution'] or '-'} | {r['source']}")
    return "\n".join(lines) + "\n"


def render_datasheet(version: str, manifest: dict, rollup: list[dict]) -> str:
    counts = manifest.get("counts", {})
    return "\n".join(
        [
            f"# Bio 3D Arena Benchmark — Datasheet ({version})",
            "",
            f"Content hash (bundle rows.json sha256): `{manifest.get('sha256', '')}`",
            "",
            "This content hash covers the benchmark bundle (`rows.json`) only.",
            "`preference_records.json` is a separate secondary file and is not covered by it.",
            "",
            "## Contents",
            f"- Tasks: {counts.get('task', 0)}",
            f"- Generators: {counts.get('generator', 0)}",
            f"- 3D outputs: {counts.get('model_output', 0)}",
            f"- Objective metrics (chamfer/F-score): {counts.get('metric', 0)}",
            "- `preference_records.json`: human pairwise votes (secondary; volume is still small).",
            "",
            "## How it was built",
            "Biological 3D generations across taxa, evaluated by held-out-scan objective metrics",
            "and human + calibrated-VLM pairwise votes. Chamfer/F-score are REFERENCE signals, not",
            "the sole ranking (morphological completeness matters — geometry alone can mislead).",
            "",
            "## Held-out ground truth",
            "The raw held-out GT point clouds are WITHHELD to preserve benchmark integrity. The",
            "release ships baked GT *reference render* GLBs only — no raw scan data.",
            "",
            "## Known limitations",
            "- Human vote volume is low; many generators are provisional (see the live /coverage).",
            "- Coverage is uneven across taxa. Metrics are front-view-biased for some tasks.",
            "",
            "## License",
            f"See LICENSE. {len(rollup)} distinct license/attribution tuples across the assets.",
            "",
        ]
    )


def dataset_composition(db: Session, category_ids: set[int] | None = None) -> dict:
    """Server-computed composition stats for the /dataset page: stat-card counts + the
    "by kingdom" / "by tier" segmented-bar breakdowns. Every number here REUSES existing
    tables (ReconTask, ModelOutput, TaskDifficulty, Category → kingdoms.KINGDOM_OF) —
    nothing is fabricated. `category_ids` optionally scopes to one kingdom's category ids
    (the caller passes `kingdoms.category_ids_for_kingdom(db, request.state.kingdom)`);
    `None` means "all kingdoms".
    """
    from .kingdoms import KINGDOM_OF, KINGDOMS

    task_q = select(Task.id, Task.category_id).where(Task.active.is_(True))
    if category_ids is not None:
        task_q = task_q.where(Task.category_id.in_(category_ids))
    task_rows = db.execute(task_q).all()
    task_ids = {r.id for r in task_rows}
    category_of_task = {r.id: r.category_id for r in task_rows}

    category_slug = dict(db.execute(select(Category.id, Category.slug)).all())

    out_q = select(ModelOutput.id, ModelOutput.task_id, ModelOutput.source).where(
        ModelOutput.hidden_at.is_(None), ModelOutput.is_gold.is_(False)
    )
    if category_ids is not None:
        out_q = out_q.where(ModelOutput.task_id.in_(task_ids))
    out_rows = db.execute(out_q).all()
    n_outputs = len(out_rows)
    provenance_types = len({r.source for r in out_rows})

    by_kingdom_counts: dict[str, int] = {k: 0 for k in KINGDOMS}
    for r in out_rows:
        slug = category_slug.get(category_of_task.get(r.task_id))
        k = KINGDOM_OF.get(slug)
        if k:
            by_kingdom_counts[k] += 1
    kingdoms_represented = sum(1 for v in by_kingdom_counts.values() if v > 0)

    recon_q = select(ReconTask.species_slug).join(Task, ReconTask.task_id == Task.id)
    if category_ids is not None:
        recon_q = recon_q.where(Task.category_id.in_(category_ids))
    ref_specimens = len(set(db.execute(recon_q).scalars()))

    tier_q = select(TaskDifficulty.task_id, TaskDifficulty.tier).join(
        Task, TaskDifficulty.task_id == Task.id
    )
    if category_ids is not None:
        tier_q = tier_q.where(Task.category_id.in_(category_ids))
    task_tier = dict(db.execute(tier_q).all())
    by_tier_counts: dict[str, int] = {"easy": 0, "moderate": 0, "hard": 0}
    for r in out_rows:
        tier = task_tier.get(r.task_id)
        if tier in by_tier_counts:
            by_tier_counts[tier] += 1

    def _bars(counts: dict[str, int]) -> list[dict]:
        total = sum(counts.values())
        return [
            {
                "key": key,
                "count": count,
                "pct": round(count / total * 100, 1) if total else 0.0,
            }
            for key, count in counts.items()
        ]

    # "By reference provenance" — coarse bucket of ModelOutput.source (already fetched
    # above for provenance_types) by its prefix before the first ':' (the convention every
    # ingestion path already follows: "api:tripo", "procedural:agrigen", "found:sketchfab",
    # "agentic:claude-opus-4", "bio3d-arena", ...). No new data collected — a display
    # grouping of the existing free-text source string, ordered by count desc, colored by
    # cycling the shared accent/semantic tokens (unbounded key set, so no fixed .ds-seg-*
    # class per bucket like by_kingdom/by_tier get).
    from collections import Counter

    _PALETTE = [
        "var(--accent)",
        "var(--accent2)",
        "var(--win)",
        "var(--amber)",
        "var(--bad)",
        "var(--faint)",
    ]
    _ACRONYMS = {"api": "API", "mri": "MRI", "ct": "CT"}

    prov_counts: Counter[str] = Counter()
    for r in out_rows:
        prefix = (r.source or "unknown").split(":", 1)[0] or "unknown"
        prov_counts[prefix] += 1
    prov_total = sum(prov_counts.values())
    by_provenance = [
        {
            "key": key,
            "label": _ACRONYMS.get(key, key.replace("-", " ").replace("_", " ").title()),
            "count": count,
            "pct": round(count / prov_total * 100, 1) if prov_total else 0.0,
            "color": _PALETTE[i % len(_PALETTE)],
        }
        for i, (key, count) in enumerate(prov_counts.most_common())
    ]

    return {
        "ref_specimens": ref_specimens,
        "kingdoms_represented": kingdoms_represented,
        "n_outputs": n_outputs,
        "provenance_types": provenance_types,
        "by_kingdom": _bars(by_kingdom_counts),
        "by_tier": _bars(by_tier_counts),
        "by_provenance": by_provenance,
    }
