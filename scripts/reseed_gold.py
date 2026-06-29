"""Re-seed gold attention-check pairs for an existing (non-fresh-seed) DB.

A gold pair shows a clearly-good output vs an obviously-bad decoy with a known answer;
an attentive voter must pick the good one (the arena serves these at config.GOLD_RATE and
uses them to score voter trust, never feeding rankings). Both referenced outputs are
is_gold=True so they stay out of normal matchmaking + rankings.

Unlike app.seed._seed_gold (synthetic good shape, for fresh DBs), this pairs a REAL
high-quality output — referenced as an anonymous is_gold copy, no file duplication — against
a degenerate-triangle decoy, so the check is maximally unambiguous (real plant vs triangle).
Idempotent: a task that already has a GoldPair is skipped. Run with the study env set."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.assets_gen import build_degenerate  # noqa: E402
from app.models import Generator, GoldPair, ModelOutput, Task  # noqa: E402
from app.sourcing import is_reference_scan, is_untextured_output  # noqa: E402

# Recon tasks with rich textured output sets — good variety for repeat voters.
DEFAULT_TASK_IDS = [11, 12, 13, 19, 20, 10]


def _vote_excluded(o: ModelOutput) -> bool:
    """Same exclusion the perceptual vote pool uses: raw scans + untextured blobs."""
    return is_reference_scan(o.source) or is_untextured_output(o)


def pick_good_output(db, task_id: int) -> ModelOutput | None:
    """Most-vetted textured, votable, non-gold GLB output for a task (the 'good' answer).

    Highest n_comparisons wins (most-seen → representative), id as a stable tiebreak. Any
    complete textured mesh trounces the triangle decoy, so the exact pick is not critical."""
    task = db.get(Task, task_id)
    if task is None:
        return None
    cands = [
        o
        for o in task.outputs
        if not o.is_gold and not _vote_excluded(o) and o.asset_format == "glb"
    ]
    if not cands:
        return None
    cands.sort(key=lambda o: (-o.n_comparisons, o.id))
    return cands[0]


def _get_or_create_calibration_generator(db) -> Generator:
    gen = db.execute(select(Generator).where(Generator.slug == "calibration")).scalars().first()
    if gen is None:
        gen = Generator(
            slug="calibration", name="Calibration (gold)", kind="decoy", is_anonymous=True
        )
        db.add(gen)
        db.flush()
    return gen


def reseed_gold(db, task_ids, *, build_decoy=build_degenerate) -> dict:
    """Create one gold pair per task (good = is_gold copy of a real output, bad = decoy).

    Returns counts. Idempotent: tasks with an existing GoldPair are skipped."""
    calib = _get_or_create_calibration_generator(db)
    asset_dir = Path(config.ASSET_DIR)
    created = skipped = 0
    detail = []
    for tid in task_ids:
        existing = db.execute(select(GoldPair).where(GoldPair.task_id == tid)).scalars().first()
        if existing is not None:
            skipped += 1
            detail.append((tid, "skip: gold pair exists"))
            continue
        good_src = pick_good_output(db, tid)
        if good_src is None:
            skipped += 1
            detail.append((tid, "skip: no votable good output"))
            continue
        # 'good' is an anonymous is_gold copy that reuses the real GLB (no file copy).
        good = ModelOutput(
            task_id=tid,
            generator_id=calib.id,
            title="gold-good",
            asset_path=good_src.asset_path,
            asset_format="glb",
            is_gold=True,
            meta_json=json.dumps({"gold": "good", "source_output_id": good_src.id}),
        )
        bad_rel = f"gold/task{tid}__bad.glb"
        build_decoy(asset_dir / bad_rel)
        bad = ModelOutput(
            task_id=tid,
            generator_id=calib.id,
            title="gold-bad",
            asset_path=bad_rel,
            asset_format="glb",
            is_gold=True,
            meta_json=json.dumps({"gold": "bad"}),
        )
        db.add_all([good, bad])
        db.flush()
        db.add(
            GoldPair(
                task_id=tid,
                good_output_id=good.id,
                bad_output_id=bad.id,
                note=f"reseed: real output {good_src.id} vs degenerate decoy",
            )
        )
        created += 1
        detail.append((tid, f"created: good<-output {good_src.id}"))
    db.commit()
    return {"created": created, "skipped": skipped, "detail": detail}


def main() -> int:
    import argparse

    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tasks",
        default=",".join(str(t) for t in DEFAULT_TASK_IDS),
        help="comma task ids to seed gold pairs for",
    )
    args = ap.parse_args()
    task_ids = [int(x) for x in args.tasks.split(",") if x.strip()]
    with SessionLocal() as db:
        res = reseed_gold(db, task_ids)
    for tid, msg in res["detail"]:
        print(f"  task {tid}: {msg}")
    print(f"gold reseed: created={res['created']} skipped={res['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
