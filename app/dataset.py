"""Content helpers for a dataset release (SP3-thin).

Pure builders for the release's preference records + LICENSE + DATASHEET text. No filesystem,
no tarball (that's scripts/build_dataset_release.py). The benchmark bundle itself comes from
scripts/export_public.py.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Comparison, Criterion, Generator, ModelOutput, Task, Vote


def build_preference_records(db: Session, comparison_ids: set[int] | None = None) -> dict:
    """Every decided comparison with full provenance — the /api/export.json payload.

    When `comparison_ids` is given, only comparisons in that set are included (used by
    scripts/build_dataset_release.py to scope the release's votes to the release bundle's
    own allowlisted tasks/generators). Default `None` keeps the full unfiltered global log,
    which is what the public /api/export.json route ships.
    """
    rows = db.execute(
        select(Vote, Comparison).join(Comparison, Vote.comparison_id == Comparison.id)
    ).all()
    records = []
    for vote, comp in rows:
        if comparison_ids is not None and comp.id not in comparison_ids:
            continue
        out_a = db.get(ModelOutput, comp.output_a_id)
        out_b = db.get(ModelOutput, comp.output_b_id)
        task = db.get(Task, comp.task_id)
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
    lines = [
        "Bio 3D Arena — Benchmark Dataset License",
        "",
        "Each 3D asset retains its original license and attribution, listed below. Assets",
        "authored by Bio 3D Arena (source=bio3d-arena) are released CC-BY-4.0. Redistribution",
        "of any asset is bound by its stated license.",
        "",
        "Per-asset provenance (license | attribution | source):",
    ]
    for r in rollup:
        lines.append(
            f"- {r['license'] or '(bio3d-arena CC-BY-4.0)'} | {r['attribution'] or '-'} | {r['source']}"
        )
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
