"""Flag geometry-only outputs (flat grey blobs) so they're excluded from the Mode-A vote pool.

Inspects every votable GLB; for each that is geometry-only (no texture/material/COLOR_0 —
see app/texture_audit.py) sets meta_json["untextured"]=True, else clears the flag. Stored in
the free-form meta_json bag (no schema change). Idempotent; --dry-run reports without writing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ModelOutput  # noqa: E402
from app.storage import get_storage  # noqa: E402
from app.texture_audit import is_geometry_only_glb  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args()

    store = get_storage()
    db = SessionLocal()
    flagged, cleared, errors = 0, 0, 0
    try:
        outs = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.is_gold.is_(False), ModelOutput.asset_format == "glb"
                )
            )
            .scalars()
            .all()
        )
        print(f"{len(outs)} votable GLB outputs to inspect")
        for o in outs:
            try:
                geo = is_geometry_only_glb(store.read(o.asset_path))
            except Exception as e:  # noqa: BLE001 — one bad asset never aborts the batch
                print(f"  error id={o.id} {o.asset_path}: {e}")
                errors += 1
                continue
            try:
                meta = json.loads(o.meta_json) if o.meta_json else {}
            except (ValueError, TypeError):
                meta = {}
            had = bool(meta.get("untextured"))
            if geo == had:
                continue  # already correct
            if geo:
                meta["untextured"] = True
                print(f"  flag   id={o.id} source={o.source} {o.asset_path}")
                flagged += 1
            else:
                meta.pop("untextured", None)
                cleared += 1
            if not args.dry_run:
                o.meta_json = json.dumps(meta)
        if not args.dry_run:
            db.commit()
    finally:
        db.close()
    verb = "would flag" if args.dry_run else "flagged"
    print(f"done: {verb} {flagged}, cleared {cleared}, errors {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
