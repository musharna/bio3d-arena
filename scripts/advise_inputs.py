"""Offline plant input advisor: per recon subject, classify growth form (seeded), look up the
capture/recon STRATEGY, and grade the current reference photo. Emits a markdown (+ optional JSON)
report. Advisory only — does not touch the recon pipeline. ANTHROPIC_API_KEY from env, never logged."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config, morphology  # noqa: E402
from app import input_grade as ig  # noqa: E402
from app.models import PlantMorphology  # noqa: E402


def advise(db, *, subjects, asset_dir, client=None, heuristics_only=False) -> list[dict]:
    """Seed morphology, then per subject grade reference/<slug>_ref.jpg against its STRATEGY.
    Missing ref / unknown subject / no-strategy → skip-and-log dict; others get a grade."""
    morphology.seed_morphology(db)
    results: list[dict] = []
    for slug in subjects:
        row = db.execute(
            select(PlantMorphology).where(PlantMorphology.subject_slug == slug)
        ).scalar_one_or_none()
        if row is None:
            results.append({"subject": slug, "skipped": "unknown subject (no morphology row)"})
            continue
        entry = morphology.STRATEGY.get(row.growth_form)
        if entry is None:
            results.append(
                {"subject": slug, "growth_form": row.growth_form, "skipped": "no strategy for form"}
            )
            continue
        ref = Path(asset_dir) / "reference" / f"{slug}_ref.jpg"
        if not ref.exists():
            results.append(
                {"subject": slug, "growth_form": row.growth_form, "skipped": f"missing ref {ref}"}
            )
            continue
        grade = ig.grade_input(
            ref.read_bytes(),
            growth_form=row.growth_form,
            strategy_entry=entry,
            client=client,
            heuristics_only=heuristics_only,
        )
        results.append(
            {"subject": slug, "growth_form": row.growth_form, "entry": entry, "grade": grade}
        )
    return results


def build_report(results: list[dict]) -> str:
    lines = ["# Plant Input Advisor — report", ""]
    for r in results:
        lines.append(f"## {r['subject']}")
        if "skipped" in r:
            lines.append(f"- SKIPPED: {r['skipped']}")
            lines.append("")
            continue
        e = r["entry"]
        g = r["grade"]
        lines.append(f"- growth form: **{r['growth_form']}**")
        lines.append(f"- recon mode: **{e.recon_mode}**")
        lines.append(
            f"- capture recipe: {e.capture_view}; {e.background}; {e.framing}; >={e.min_px}px"
        )
        lines.append(f"- expected failure: {e.expected_failure}")
        lines.append(f"- nvs hint: {e.nvs_pose_hint}")
        lines.append(
            f"- photo grade: **{g.verdict}** ({g.width}x{g.height}, dims_ok={g.dims_ok}, "
            f"bg_ok={g.bg_ok}, bg_uniformity={g.bg_uniformity:.3f})"
        )
        if g.vlm is not None:
            lines.append(f"- VLM: {g.vlm} | growth_form_match={g.growth_form_match}")
        if g.reasons:
            lines.append(f"- reasons: {'; '.join(g.reasons)}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    import argparse
    import datetime as dt
    import json
    import os

    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subject", choices=sorted(morphology.SEED), default=None)
    ap.add_argument("--heuristics-only", action="store_true", help="skip the VLM grader")
    ap.add_argument("--json", action="store_true", help="also write a JSON sidecar")
    args = ap.parse_args()

    subjects = [args.subject] if args.subject else list(morphology.SEED)

    client = None
    heuristics_only = args.heuristics_only
    note = ""
    if not heuristics_only:
        if os.environ.get("ANTHROPIC_API_KEY"):
            import anthropic

            client = anthropic.Anthropic()
        else:
            heuristics_only = True
            note = "ANTHROPIC_API_KEY not set — heuristics-only run."

    with SessionLocal() as db:
        results = advise(
            db,
            subjects=subjects,
            asset_dir=config.ASSET_DIR,
            client=client,
            heuristics_only=heuristics_only,
        )

    md = build_report(results)
    if note:
        md = f"> {note}\n\n" + md
    out_dir = Path("docs/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    (out_dir / f"{stamp}-input-advisor.md").write_text(md)
    if args.json:
        serializable = [
            {k: (vars(v) if hasattr(v, "__dict__") else v) for k, v in r.items() if k != "entry"}
            for r in results
        ]
        (out_dir / f"{stamp}-input-advisor.json").write_text(
            json.dumps(serializable, indent=2, default=str)
        )
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
