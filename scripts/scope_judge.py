"""Mode-C scope pass: classify each rubric'd model into which plant parts it actually depicts
(is_plant + visible_parts), persisted as ModelScope. Downstream, scope.is_assessable then keeps
a trait from being judged on a model that doesn't show it (e.g. habit on a single-fruit tomato).

One cheap VLM call per model, reusing the same multi-view contact sheet as trait_judge. Resumable
(skips outputs already scoped for this judge_model) and capped (--max). enumerate_outputs/run_batch
are import-testable with an injected classify_fn + sheet_b64; main() wires Playwright + Anthropic.
Mirrors scripts/trait_judge.py."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import judge, judge_render, scope  # noqa: E402
from app.models import ModelScope  # noqa: E402

GRID_CONDITION = "multi4"
JUDGE_MODEL = judge.JUDGE_MODEL


def enumerate_outputs(db, task_ids) -> list[dict]:
    """One row per distinct output covered by trait_judge (same filters), carrying what the
    scope classifier needs: output_id, species (taxon), prompt. Deduped by output_id."""
    from scripts.trait_judge import enumerate_work

    seen: set[int] = set()
    items: list[dict] = []
    for w in enumerate_work(db, task_ids):
        oid = w["output_id"]
        if oid in seen:
            continue
        seen.add(oid)
        items.append({"output_id": oid, "species": w["species"], "prompt": w["prompt"]})
    return items


def existing_scoped(db, judge_model: str) -> set[int]:
    return {
        s.output_id
        for s in db.execute(select(ModelScope)).scalars()
        if s.judge_model == judge_model
    }


def run_batch(
    db,
    *,
    classify_fn,
    sheet_b64,
    work,
    judge_model: str = JUDGE_MODEL,
    max_outputs: int | None = None,
) -> dict:
    """For each output call classify_fn(species, prompt, sheet_b64(oid)) → {is_plant,
    visible_parts, rationale} and persist one ModelScope. Skips already-scoped outputs before
    the paid call. Per-output commit; counts written/skipped/errors."""
    seen = existing_scoped(db, judge_model)
    written = skipped = errors = 0
    done = 0
    for item in work:
        if max_outputs is not None and done >= max_outputs:
            break
        oid = item["output_id"]
        if oid in seen:
            skipped += 1
            continue
        try:
            b64 = sheet_b64(oid)
            res = classify_fn(item["species"], item["prompt"], b64)
            db.add(
                ModelScope(
                    output_id=oid,
                    is_plant=bool(res["is_plant"]),
                    parts_json=json.dumps(res.get("visible_parts", [])),
                    rationale=res.get("rationale", ""),
                    judge_model=judge_model,
                )
            )
            db.commit()
            seen.add(oid)
            written += 1
            done += 1
        except Exception as e:  # noqa: BLE001 — best-effort batch: count + continue
            db.rollback()
            errors += 1
            print(f"scope-judge error on output {oid}: {e}", file=sys.stderr)
    return {"written": written, "skipped": skipped, "errors": errors}


def _real_sheet_b64_factory(db, capture_multi):
    def sheet_b64(output_id: int) -> str:
        from app import config

        judge_render.render_contact_sheets(
            db, [output_id], GRID_CONDITION, capture_multi=capture_multi
        )
        path = Path(config.ASSET_DIR) / judge_render.contact_sheet_path(output_id, GRID_CONDITION)
        if not (path.exists() and path.stat().st_size > 0):
            raise RuntimeError(f"no contact sheet for output {output_id}")
        return base64.b64encode(path.read_bytes()).decode()

    return sheet_b64


def main() -> int:
    import argparse

    from app.database import SessionLocal
    from app.models import TraitRubric

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default="", help="comma task ids (default: all with a rubric)")
    ap.add_argument("--max", type=int, default=None, help="cap outputs classified this run")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print how many outputs still need a scope call and exit (no API/browser)",
    )
    args = ap.parse_args()

    with SessionLocal() as db:
        if args.tasks.strip():
            task_ids = [int(x) for x in args.tasks.split(",") if x.strip()]
        else:
            task_ids = [r.task_id for r in db.execute(select(TraitRubric)).scalars() if r.task_id]
        work = enumerate_outputs(db, task_ids)
        seen = existing_scoped(db, JUDGE_MODEL)
        needed = [w for w in work if w["output_id"] not in seen]
        print(
            f"scope-judge: {len(work)} outputs; {len(needed)} need a call "
            f"(≈ API calls); {len(work) - len(needed)} already scoped"
        )
        if args.dry_run:
            return 0

    import anthropic

    from scripts.judge_capture import browser_capture_multi_factory

    client = anthropic.Anthropic()

    def classify_fn(species, prompt, sheet_b64):
        return scope.classify_scope(client, species=species, prompt=prompt, sheet_b64=sheet_b64)

    with SessionLocal() as db:
        capture_multi = browser_capture_multi_factory()
        sheet_b64 = _real_sheet_b64_factory(db, capture_multi)
        res = run_batch(
            db, classify_fn=classify_fn, sheet_b64=sheet_b64, work=work, max_outputs=args.max
        )
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
