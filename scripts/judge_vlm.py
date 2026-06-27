"""VLM-judge batch driver: enumerate comparisons, render sheets, judge, persist.

Resumable (skips swap-group/order rows already in JudgeVote) and capped (--max).
enumerate_work/run_batch are import-testable with injected judge_fn + sheet_b64;
main() wires the real Playwright renderer + Anthropic client and runs via jobd."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import judge, judge_render  # noqa: E402
from app.calibration import STUDY_CRITERIA  # noqa: E402
from app.matchmaking import _real_outputs  # noqa: E402
from app.models import (  # noqa: E402
    CalibrationPair,
    Criterion,
    JudgeVote,
    Task,
)

GRID_CONDITION = "multi4"


def enumerate_work(db, grid_condition: str = GRID_CONDITION, criteria_slugs=None) -> list[dict]:
    """Ordered work rows (two per logical comparison, sharing a swap_group)."""
    criteria_slugs = criteria_slugs or STUDY_CRITERIA
    crit_by_slug = {
        c.slug: c
        for c in db.execute(select(Criterion).where(Criterion.slug.in_(criteria_slugs))).scalars()
    }
    items: list[dict] = []

    def add(task_id, a, b, crit, condition):
        grp = judge.swap_group_id(task_id, a, b, crit.id, condition)
        for oa, ob in ((a, b), (b, a)):
            items.append(
                {
                    "task_id": task_id,
                    "output_a_id": oa,
                    "output_b_id": ob,
                    "criterion_id": crit.id,
                    "criterion_slug": crit.slug,
                    "condition": condition,
                    "swap_group": grp,
                }
            )

    # Grid: every task pair × criteria, under the single grid condition.
    for task in db.execute(select(Task).where(Task.active.is_(True))).scalars():
        outs = sorted(o.id for o in _real_outputs(task))
        for i in range(len(outs)):
            for j in range(i + 1, len(outs)):
                for slug in criteria_slugs:
                    if slug in crit_by_slug:
                        add(task.id, outs[i], outs[j], crit_by_slug[slug], grid_condition)

    # Calibration subset: each pair × ALL conditions (the perception ladder).
    for cp in db.execute(select(CalibrationPair)).scalars():
        crit = db.get(Criterion, cp.criterion_id)
        if crit is None:
            continue
        a, b = sorted((cp.output_a_id, cp.output_b_id))
        for condition in judge_render.CONDITIONS:
            add(cp.task_id, a, b, crit, condition)
    return items


def existing_swap_orders(db) -> set:
    return {
        (v.swap_group, v.output_a_id, v.output_b_id)
        for v in db.execute(select(JudgeVote)).scalars()
    }


def run_batch(
    db,
    *,
    judge_fn,
    sheet_b64,
    grid_condition: str = GRID_CONDITION,
    criteria_slugs=None,
    max_votes: int | None = None,
) -> dict:
    """judge_fn(species, prompt, criterion_name, criterion_desc, a_b64, b_b64)->(winner,rationale).
    sheet_b64(output_id, condition)->base64 PNG string."""
    work = enumerate_work(db, grid_condition, criteria_slugs)
    seen = existing_swap_orders(db)
    written = skipped = errors = 0
    for item in work:
        key = (item["swap_group"], item["output_a_id"], item["output_b_id"])
        if key in seen:
            skipped += 1
            continue
        if max_votes is not None and written >= max_votes:
            break
        task = db.get(Task, item["task_id"])
        crit = db.get(Criterion, item["criterion_id"])
        try:
            a_b64 = sheet_b64(item["output_a_id"], item["condition"])
            b_b64 = sheet_b64(item["output_b_id"], item["condition"])
            winner, rationale = judge_fn(
                task.category.name if task.category else "",
                task.prompt,
                crit.name,
                crit.description,
                a_b64,
                b_b64,
            )
            db.add(
                JudgeVote(
                    task_id=item["task_id"],
                    output_a_id=item["output_a_id"],
                    output_b_id=item["output_b_id"],
                    criterion_id=item["criterion_id"],
                    winner=winner,
                    view_condition=item["condition"],
                    judge_model=judge.JUDGE_MODEL,
                    swap_group=item["swap_group"],
                    rationale=rationale,
                )
            )
            db.commit()
            seen.add(key)
            written += 1
        except Exception as e:  # noqa: BLE001 — best-effort; count + continue
            db.rollback()
            errors += 1
            print(f"judge error on {key}: {e}", file=sys.stderr)
    return {"written": written, "skipped": skipped, "errors": errors}


def _real_sheet_b64_factory(db, capture_multi):
    """Render-on-demand sheet provider for production runs."""

    def sheet_b64(output_id: int, condition: str) -> str:
        judge_render.render_contact_sheets(db, [output_id], condition, capture_multi=capture_multi)
        from app import config

        path = Path(config.ASSET_DIR) / judge_render.contact_sheet_path(output_id, condition)
        return base64.b64encode(path.read_bytes()).decode()

    return sheet_b64


def main() -> int:
    import argparse

    import anthropic

    from app.database import SessionLocal
    from scripts.judge_capture import browser_capture_multi_factory

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max", type=int, default=None, help="cap votes written this run")
    args = ap.parse_args()

    client = anthropic.Anthropic()

    def judge_fn(species, prompt, cname, cdesc, a_b64, b_b64):
        return judge.judge_pair(
            client,
            species=species,
            prompt=prompt,
            criterion_name=cname,
            criterion_desc=cdesc,
            sheet_a_b64=a_b64,
            sheet_b_b64=b_b64,
        )

    with SessionLocal() as db:
        capture_multi = browser_capture_multi_factory()
        sheet_b64 = _real_sheet_b64_factory(db, capture_multi)
        res = run_batch(db, judge_fn=judge_fn, sheet_b64=sheet_b64, max_votes=args.max)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
