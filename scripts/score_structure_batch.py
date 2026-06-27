"""Batch organ-structure scorer: populate OrganMetric for every structure-known output.

Iterates ModelOutputs through `structure_service.score_and_store`, which self-gates:
non-procedural / no-declared-record outputs are skipped (N/A, no row), procedural
outputs with a record get an OrganMetric (status scored|no_reference|error). This is
the batch counterpart to the per-output service + the 5-record live smoke
(scripts/smoke_score_structure.py). Pure consumer of the existing tested service —
no AgriGen code here; the scorer is reached via config.RECON_SCORER_URL at run time.

Run (needs a live /score_structure — no GT bundle required for this axis):
    BIO3D_RECON_SCORER_URL=http://127.0.0.1:8077 \
      .venv/bin/python scripts/score_structure_batch.py [--only-missing] [--max N]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import structure_service  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ModelOutput, OrganMetric  # noqa: E402


def run_batch(db, *, scorer=None, only_missing: bool = False, max_calls: int | None = None) -> dict:
    """Score every structure-known output → upsert OrganMetric. Returns a tally.

    scorer: injectable /score_structure callable (defaults to the live one).
    only_missing: skip outputs that already have an OrganMetric row (resumable).
    max_calls: cap scorer calls this run (chunking); None = no cap.
    """
    if scorer is None:
        scorer = structure_service._default_scorer
    have_metric = {oid for (oid,) in db.execute(select(OrganMetric.output_id)).all()}
    tally = {"scored": 0, "no_reference": 0, "errors": 0, "skipped": 0, "skipped_existing": 0}
    for out in db.execute(select(ModelOutput)).scalars():
        if only_missing and out.id in have_metric:
            tally["skipped_existing"] += 1
            continue
        if (
            max_calls is not None
            and (tally["scored"] + tally["no_reference"] + tally["errors"]) >= max_calls
        ):
            break
        m = structure_service.score_and_store(db, out, scorer=scorer)
        if m is None:
            tally["skipped"] += 1  # N/A on this axis (non-procedural / no record)
        elif m.status == "scored":
            tally["scored"] += 1
        elif m.status == "no_reference":
            tally["no_reference"] += 1
        else:
            tally["errors"] += 1
    db.commit()
    return tally


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--only-missing", action="store_true", help="skip outputs that already have an OrganMetric"
    )
    ap.add_argument("--max", type=int, default=None, help="cap scorer calls this run (chunking)")
    args = ap.parse_args()
    with SessionLocal() as db:
        res = run_batch(db, only_missing=args.only_missing, max_calls=args.max)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
