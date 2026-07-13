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


def enumerate_work(
    db,
    grid_condition: str = GRID_CONDITION,
    criteria_slugs=None,
    *,
    calibration_only: bool = False,
) -> list[dict]:
    """Ordered work rows (two per logical comparison, sharing a swap_group).

    calibration_only=True skips the full grid and emits only the calibration ladder
    (each CalibrationPair × all conditions). The grid is the multi4-only block that
    would otherwise duplicate each calibration pair's multi4 row, so skipping it both
    bounds the run and leaves the calibration rows un-duplicated."""
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
    if not calibration_only:
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


def enumerate_sample(
    db,
    task_ids,
    *,
    criterion_slug: str = "overall",
    condition: str = GRID_CONDITION,
    per_output_k: int = 3,
) -> list[dict]:
    """Bounded connected pair sample for ONE criterion/condition over the given tasks.

    Densifies the per-tier perceptual ranking without the O(n²) full grid. Excludes Mode-A
    outputs (reference scans + untextured) — they're not ranked, so judging them wastes calls.
    Pairs form a circulant graph (each output vs its next k) → connected (so Bradley-Terry can
    rank every node) at ~n·k/2 pairs instead of n²/2. Two ordered rows per pair (swap_group).

    Bradley-Terry ranks GENERATORS, not outputs, so a pair of two outputs from the SAME generator
    is a self-edge: it tells the fit nothing and burns a real VLM call. A generator often owns
    several outputs on one task, so the raw circulant pairs models against themselves. Here a
    same-generator ring neighbour is skipped (never emitted) and the walk advances to the next
    eligible partner from a DIFFERENT generator — so each output still spends its full
    `per_output_k` budget on genuinely different models, and the generator-level graph stays
    connected (the "next different generator" edges chain around the ring through every
    same-generator run). A task whose eligible outputs all come from one generator yields no
    pairs — correct: a model cannot be compared with itself.
    """
    from app.sourcing import is_reference_scan, is_untextured_output

    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    items: list[dict] = []

    def add(task_id, a, b):
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

    for tid in task_ids:
        task = db.get(Task, tid)
        if task is None:
            continue
        eligible = sorted(
            (
                o
                for o in _real_outputs(task)
                if not is_reference_scan(o.source) and not is_untextured_output(o)
            ),
            key=lambda o: o.id,
        )
        ids = [o.id for o in eligible]
        gens = [o.generator_id for o in eligible]
        n = len(ids)
        if n < 2:
            continue
        seen_pairs: set[tuple[int, int]] = set()
        for i in range(n):
            budget = per_output_k
            # Walk the ring forward, skipping same-generator neighbours (self-edges), until this
            # output has `budget` partners from different generators or the ring is exhausted.
            for d in range(1, n):
                if budget <= 0:
                    break
                j = (i + d) % n
                if gens[j] == gens[i]:
                    continue
                budget -= 1
                a, b = sorted((ids[i], ids[j]))
                if (a, b) in seen_pairs:
                    continue
                seen_pairs.add((a, b))
                add(tid, a, b)
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
    calibration_only: bool = False,
    work=None,
) -> dict:
    """judge_fn(species, prompt, criterion_name, criterion_desc, a_b64, b_b64)->(winner,rationale).
    sheet_b64(output_id, condition)->base64 PNG string. Pass `work` to run over a prebuilt work
    list (e.g. enumerate_sample); otherwise the full grid / calibration ladder is enumerated."""
    if work is None:
        work = enumerate_work(db, grid_condition, criteria_slugs, calibration_only=calibration_only)
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
        res = judge_render.render_contact_sheets(
            db, [output_id], condition, capture_multi=capture_multi
        )
        from app import config

        path = Path(config.ASSET_DIR) / judge_render.contact_sheet_path(output_id, condition)
        if not (path.exists() and path.stat().st_size > 0):
            # Surface the real render cause instead of a misleading FileNotFoundError on read.
            detail = next(
                (f["error"] for f in res["failures"] if f["oid"] == output_id), "no sheet written"
            )
            raise RuntimeError(f"render failed for output {output_id} ({condition}): {detail}")
        return base64.b64encode(path.read_bytes()).decode()

    return sheet_b64


def main() -> int:
    import argparse

    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max", type=int, default=None, help="cap votes written this run")
    ap.add_argument(
        "--calibration-only",
        action="store_true",
        help="judge only the calibration ladder (skip the full pairwise grid)",
    )
    ap.add_argument(
        "--sample",
        action="store_true",
        help="bounded connected pair sample (one criterion) over --tasks, not the full grid",
    )
    ap.add_argument("--tasks", default="10,11,12,13,19", help="comma task ids for --sample")
    ap.add_argument("--criterion", default="overall", help="criterion slug for --sample")
    ap.add_argument("--per-output-k", type=int, default=3, help="neighbors per output in --sample")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="with --sample: print the uncovered call count and exit (no API/browser)",
    )
    args = ap.parse_args()

    # Build the sample work list up front; --dry-run reports the count without any API/browser.
    sample_work = None
    if args.sample:
        task_ids = [int(x) for x in args.tasks.split(",") if x.strip()]
        with SessionLocal() as db:
            sample_work = enumerate_sample(
                db,
                task_ids,
                criterion_slug=args.criterion,
                per_output_k=args.per_output_k,
            )
            seen = existing_swap_orders(db)
            uncovered = sum(
                1
                for w in sample_work
                if (w["swap_group"], w["output_a_id"], w["output_b_id"]) not in seen
            )
        pairs = len(sample_work) // 2
        print(
            f"sample scope: tasks={task_ids} criterion={args.criterion!r} k={args.per_output_k} "
            f"→ {pairs} pairs / {len(sample_work)} ordered rows; {uncovered} uncovered (≈ API calls)"
        )
        if args.dry_run:
            return 0

    import anthropic

    from scripts.judge_capture import browser_capture_multi_factory

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
        res = run_batch(
            db,
            judge_fn=judge_fn,
            sheet_b64=sheet_b64,
            max_votes=args.max,
            calibration_only=args.calibration_only,
            work=sample_work,
        )
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
