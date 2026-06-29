"""Mode-C trait-judge batch driver: enumerate rubric'd outputs, render a multi-view
contact sheet per output, VLM-check it against the taxon rubric, persist TraitVerdicts.

Resumable (skips (output_id, trait_key, judge_model) rows already in TraitVerdict) and
capped (--max outputs). enumerate_work/run_batch are import-testable with an injected
check_fn + sheet_b64; main() wires the real Playwright renderer + Anthropic client.
Mirrors scripts/judge_vlm.py."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import judge, judge_render  # noqa: E402
from app.models import (  # noqa: E402
    Task,
    TraitRubric,
    TraitVerdict,
)
from app.sourcing import is_reference_scan, is_untextured_output  # noqa: E402

GRID_CONDITION = "multi4"
JUDGE_MODEL = judge.JUDGE_MODEL


def enumerate_work(db, task_ids) -> list[dict]:
    """One work row per (output, rubric) for non-gold, non-reference-scan, non-untextured
    outputs of tasks that HAVE a TraitRubric. Each row carries everything check_fn needs:
    output_id, task_id, rubric_id, species (taxon), prompt, traits (parsed rubric list)."""
    items: list[dict] = []
    for tid in task_ids:
        rubric = db.execute(select(TraitRubric).where(TraitRubric.task_id == tid)).scalars().first()
        if rubric is None:
            continue
        task = db.get(Task, tid)
        if task is None:
            continue
        try:
            traits = json.loads(rubric.traits_json or "[]")
        except (ValueError, TypeError):
            traits = []
        if not traits:
            continue
        for out in task.outputs:
            if out.is_gold:
                continue
            if is_reference_scan(out.source) or is_untextured_output(out):
                continue
            items.append(
                {
                    "output_id": out.id,
                    "task_id": tid,
                    "rubric_id": rubric.id,
                    "species": rubric.taxon,
                    "prompt": task.prompt,
                    "traits": traits,
                }
            )
    return items


def existing_keys(db) -> set:
    """(output_id, trait_key, judge_model) tuples already persisted."""
    return {
        (v.output_id, v.trait_key, v.judge_model)
        for v in db.execute(select(TraitVerdict)).scalars()
    }


def run_batch(
    db,
    *,
    check_fn,
    sheet_b64,
    work=None,
    task_ids=None,
    judge_model: str = JUDGE_MODEL,
    max_outputs: int | None = None,
) -> dict:
    """For each work output, call check_fn(species, prompt, sheet_b64(output_id), traits) and
    persist one TraitVerdict per returned trait (skipping (output_id, trait_key, judge_model)
    already stored). Per-output commit; counts written/skipped/errors. Outputs whose sheet
    fails to render (sheet_b64 raises) are counted as errors and skipped, not crashed on."""
    if work is None:
        work = enumerate_work(db, task_ids or [])
    seen = existing_keys(db)
    written = skipped = errors = 0
    outputs_done = 0
    for item in work:
        if max_outputs is not None and outputs_done >= max_outputs:
            break
        oid = item["output_id"]
        try:
            b64 = sheet_b64(oid)
            results = check_fn(item["species"], item["prompt"], b64, item["traits"])
            wrote_here = 0
            for r in results:
                key = (oid, r["trait_key"], judge_model)
                if key in seen:
                    skipped += 1
                    continue
                db.add(
                    TraitVerdict(
                        output_id=oid,
                        rubric_id=item["rubric_id"],
                        trait_key=r["trait_key"],
                        trait_class=r["trait_class"],
                        verdict=r["verdict"],
                        rationale=r.get("rationale", ""),
                        judge_model=judge_model,
                    )
                )
                seen.add(key)
                written += 1
                wrote_here += 1
            db.commit()
            if wrote_here:
                outputs_done += 1
        except Exception as e:  # noqa: BLE001 — best-effort batch: count + continue
            db.rollback()
            errors += 1
            print(f"trait-judge error on output {oid}: {e}", file=sys.stderr)
    return {"written": written, "skipped": skipped, "errors": errors}


def _real_sheet_b64_factory(db, capture_multi):
    """Render-on-demand multi4 contact-sheet provider for production runs."""

    def sheet_b64(output_id: int) -> str:
        res = judge_render.render_contact_sheets(
            db, [output_id], GRID_CONDITION, capture_multi=capture_multi
        )
        from app import config

        path = Path(config.ASSET_DIR) / judge_render.contact_sheet_path(output_id, GRID_CONDITION)
        if not (path.exists() and path.stat().st_size > 0):
            detail = next(
                (f["error"] for f in res["failures"] if f["oid"] == output_id), "no sheet written"
            )
            raise RuntimeError(f"render failed for output {output_id}: {detail}")
        return base64.b64encode(path.read_bytes()).decode()

    return sheet_b64


def main() -> int:
    import argparse

    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tasks", default="", help="comma task ids to judge (default: all with rubric)"
    )
    ap.add_argument("--max", type=int, default=None, help="cap outputs judged this run")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the uncovered (output, trait) call count and exit (no API/browser)",
    )
    args = ap.parse_args()

    with SessionLocal() as db:
        if args.tasks.strip():
            task_ids = [int(x) for x in args.tasks.split(",") if x.strip()]
        else:
            task_ids = [r.task_id for r in db.execute(select(TraitRubric)).scalars() if r.task_id]
        work = enumerate_work(db, task_ids)
        seen = existing_keys(db)
        uncovered = sum(
            1
            for w in work
            for t in w["traits"]
            if (w["output_id"], t["key"], JUDGE_MODEL) not in seen
        )
        print(
            f"trait-judge scope: tasks={task_ids} → {len(work)} outputs; "
            f"{uncovered} uncovered (output,trait) verdicts (≈ trait rows; 1 API call/output)"
        )
        if args.dry_run:
            return 0

    import anthropic

    from scripts.judge_capture import browser_capture_multi_factory

    from app import traits as traits_mod

    client = anthropic.Anthropic()

    def check_fn(species, prompt, sheet_b64, rubric_traits):
        return traits_mod.check_traits(
            client, species=species, prompt=prompt, sheet_b64=sheet_b64, traits=rubric_traits
        )

    with SessionLocal() as db:
        capture_multi = browser_capture_multi_factory()
        sheet_b64 = _real_sheet_b64_factory(db, capture_multi)
        res = run_batch(
            db,
            check_fn=check_fn,
            sheet_b64=sheet_b64,
            work=work,
            max_outputs=args.max,
        )
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
