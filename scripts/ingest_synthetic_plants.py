#!/usr/bin/env python3
"""Register generated-plant GLBs into the synthetic-plants botanical-plausibility arena.

GLB names follow `<species_slug>__<generator>[__<id>].glb` (same as the recon bake-off, so the
recon GLBs can seed the cross-paradigm matchup). Resolves each to its synth-plant type Task (by
species slug) and registers it under its generator (content-deduped). Votes-only — no scoring;
the existing arena ranks generators by botanical-plausibility votes.

Usage:
    python scripts/ingest_synthetic_plants.py --dir /path/to/generated_plant_glbs
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="dir of <species>__<generator>[__<id>].glb files")
    args = ap.parse_args()

    from app import ingest, seed
    from app.database import SessionLocal, init_db
    from scripts.ingest_bakeoff import parse_bakeoff_name

    init_db()
    db = SessionLocal()
    seed.seed_synthetic_plants(db)  # idempotent — ensure the scope exists
    db.commit()

    n = 0
    for path in sorted(glob.glob(os.path.join(args.dir, "*.glb"))):
        base = os.path.basename(path)[: -len(".glb")]
        parsed = parse_bakeoff_name(base)
        if parsed is None:
            print(f"  SKIP {base}: expected <species>__<generator>[__<id>].glb")
            continue
        species, generator, _photo = parsed
        task = seed.synth_task_for_slug(db, species)
        if task is None:
            print(f"  SKIP {base}: no synthetic-plants task for '{species}'")
            continue
        out, created = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug=generator,
            data=Path(path).read_bytes(),
            ext="glb",
            title=f"{species} — {generator}",
            meta={"synthetic": True},
        )
        n += 1
        print(f"  ingested {base} -> output #{out.id} (created={created})")
    db.commit()
    db.close()
    print(f"ingested {n} GLB(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
