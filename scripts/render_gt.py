"""Bake held-out GT scans → reference GLBs in bio3d storage (run once at build time).

Reads the scorer's GT bundle (config.GT_BUNDLE_DIR), converts one representative scan per
recon-task species to a +Y-up POINTS GLB, and saves it under the `gt/` asset subdir. The
running server then serves these via reference_for_task — it never reads the bundle itself.
Idempotent: re-running overwrites the baked GLBs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.gt_render import bake_species_gt, representative_gt_npy  # noqa: E402
from app.models import ReconTask  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        species = sorted(
            {rt.species_slug for rt in db.execute(select(ReconTask)).scalars() if rt.species_slug}
        )
    finally:
        db.close()

    if not species:
        print("no ReconTask species in the DB — nothing to bake")
        return 0

    print(f"GT bundle: {config.GT_BUNDLE_DIR}")
    baked, skipped = 0, 0
    for sp in species:
        npy = representative_gt_npy(sp)
        if npy is None:
            print(f"  skip {sp}: no GT .npy under {config.GT_BUNDLE_DIR / sp}")
            skipped += 1
            continue
        rel = bake_species_gt(sp)
        print(f"  baked {sp} -> {rel} (from {npy.name})")
        baked += 1
    print(f"done: baked {baked}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
