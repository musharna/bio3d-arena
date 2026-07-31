#!/usr/bin/env python3
"""Register generated-plant GLBs into the synthetic-plants botanical-plausibility arena.

GLB names follow `<species_slug>__<generator>[__<id>].glb` (same as the recon bake-off, so the
recon GLBs can seed the cross-paradigm matchup). Resolves each to its synth-plant type Task (by
species slug) and registers it under its generator (content-deduped). Votes-only — no scoring;
the existing arena ranks generators by botanical-plausibility votes.

Usage:
    python scripts/ingest_synthetic_plants.py --dir /path/to/glbs --paradigm image_recon

`--paradigm` is required rather than defaulted. This script exists to make entrants VOTABLE,
and the arena vote pool is an allowlist over paradigms — so a generator ingested without one
is stored, displayed, and then never served for voting. There is no majority case to default
to either: the docstring above says the recon bake-off GLBs are deliberately reused here
alongside genuinely generated plants, so any default would silently file half the corpus onto
the wrong board. Every GLB in one run is assumed to share a paradigm; ingest separate runs per
paradigm if a directory mixes them.
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
    ap.add_argument(
        "--paradigm",
        required=True,
        help="what kind of entrant these GLBs are, e.g. image_recon or text_native. "
        "Required: a generator ingested without one is never served for voting.",
    )
    args = ap.parse_args()

    from app import config, ingest, seed
    from app.database import SessionLocal, init_db
    from scripts.ingest_bakeoff import parse_bakeoff_name

    # Say so loudly rather than producing a corpus nobody can vote on. Not an error: the
    # off-roster paradigms are legitimate (they keep everything except an arena slot).
    if config.ARENA_VOTE_PARADIGMS and args.paradigm not in config.ARENA_VOTE_PARADIGMS:
        print(
            f"NOTE: paradigm '{args.paradigm}' is not on the arena vote roster "
            f"({sorted(config.ARENA_VOTE_PARADIGMS)}), so these entrants will be ingested and "
            "displayed but never served for voting."
        )

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
            paradigm=args.paradigm,
        )
        n += 1
        print(f"  ingested {base} -> output #{out.id} (created={created})")
    db.commit()
    db.close()
    print(f"ingested {n} GLB(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
