#!/usr/bin/env python3
"""Ingest a recon bake-off (TRELLIS/Hunyuan3D × species GLBs) into Bio 3D Arena and
score it against the live recon service — the B5 launch step for the Mode-B benchmark.

The bake-off dir holds `<species_slug>__<method>.glb` files (e.g.
`arabidopsis_thaliana__trellis.glb`) plus a manifest; the species slugs MUST match the
seeded ReconTask bindings (see app/seed.py:seed_recon_benchmark). This script is
idempotent: it ensures the recon scaffolding exists (without wiping the DB), registers
each GLB on its species Task under its method Generator (deduped by content), then runs
the batch scorer so the /benchmark Mode-B board fills in.

Usage:
    # 1. start the AgriGen scorer:
    #    cd ~/agrigen/backend && AGRIGEN_GT_BUNDLE=data/gt_bundle_prod \\
    #      .venv/bin/uvicorn agrigen.scoring_service.app:app --port 8077
    # 2. ingest + score:
    BIO3D_RECON_SCORER_URL=http://localhost:8077 \\
      python scripts/ingest_bakeoff.py --bakeoff-dir /path/to/bakeoff_v1
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
    ap.add_argument("--bakeoff-dir", required=True, help="dir of <species>__<method>.glb files")
    ap.add_argument(
        "--scorer-url",
        default=os.environ.get("BIO3D_RECON_SCORER_URL"),
        help="recon /score service base URL (default: config.RECON_SCORER_URL)",
    )
    ap.add_argument("--no-rescore", action="store_true", help="ingest only, skip scoring")
    args = ap.parse_args()

    from app import config, ingest, recon_service, seed
    from app.database import SessionLocal, init_db
    from app.models import ReconTask

    if args.scorer_url:
        config.RECON_SCORER_URL = args.scorer_url

    init_db()
    db = SessionLocal()
    seed.seed_recon_benchmark(db)  # idempotent — ensure species Tasks + slug map + gens
    db.commit()

    slug_to_task = {rt.species_slug: rt.task_id for rt in db.query(ReconTask).all()}
    ingested = 0
    for path in sorted(glob.glob(os.path.join(args.bakeoff_dir, "*.glb"))):
        base = os.path.basename(path)[: -len(".glb")]
        if "__" not in base:
            print(f"  SKIP {base}: expected <species>__<method>.glb")
            continue
        species, method = base.split("__", 1)
        task_id = slug_to_task.get(species)
        if task_id is None:
            print(f"  SKIP {base}: no seeded Task for species '{species}'")
            continue
        data = Path(path).read_bytes()
        out, created = ingest.register_output(
            db,
            task_id=task_id,
            generator_slug=method,
            data=data,
            ext="glb",
            title=f"{species} — {method}",
            meta={"bakeoff": os.path.basename(args.bakeoff_dir.rstrip("/")), "method": method},
        )
        ingested += 1
        print(f"  ingested {base} -> output #{out.id} ({len(data)} bytes, created={created})")
    db.commit()
    print(f"ingested {ingested} GLB(s)")

    if not args.no_rescore:
        detail = recon_service.rescore_all(db)
        print(f"rescore: {detail}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
