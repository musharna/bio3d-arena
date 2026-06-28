"""Fix reference-scan GLBs that render upside-down (ingested without +Z-up → +Y-up rotation).

Walks every reference-scan ModelOutput (is_reference_scan), and for any whose GLB is raw
+Z-up (never recentred) re-orients it in place, overwriting the stored asset. Idempotent and
auditable: it prints exactly which outputs were changed and which were already correct, and a
second run changes nothing. Pass --dry-run to report without writing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import ModelOutput  # noqa: E402
from app.reorient import _scene_points, needs_reorient, reorient_glb_bytes  # noqa: E402
from app.sourcing import is_reference_scan, is_z_up_scan  # noqa: E402
from app.storage import get_storage  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args()

    store = get_storage()
    db = SessionLocal()
    fixed, ok, errors, skipped = 0, 0, 0, 0
    skipped_sources: set[str] = set()
    try:
        outs = db.execute(select(ModelOutput)).scalars().all()
        refs = [o for o in outs if is_reference_scan(o.source) and o.asset_format == "glb"]
        print(f"{len(refs)} reference-scan GLB outputs to check")
        for o in refs:
            try:
                glb = store.read(o.asset_path)
            except Exception as e:  # noqa: BLE001 — one bad asset never aborts the batch
                print(f"  error  id={o.id} source={o.source} {o.asset_path}: {e}")
                errors += 1
                continue
            # Only auto-reorient sources whose +Z-up convention is confirmed. An un-recentred
            # cloud from an unverified source (e.g. icrisat-legume, X-up) is reported, not rotated.
            if not is_z_up_scan(o.source):
                verts, _ = _scene_points(glb)
                if needs_reorient(verts):
                    print(
                        f"  SKIP   id={o.id} source={o.source} {o.asset_path}: "
                        "un-recentred but up-axis not verified Z-up — left as-is"
                    )
                    skipped += 1
                    skipped_sources.add(o.source or "")
                else:
                    ok += 1
                continue
            new = reorient_glb_bytes(glb)
            if new is None:
                ok += 1
                continue
            tag = "would fix" if args.dry_run else "fixed"
            print(f"  {tag}  id={o.id} source={o.source} {o.asset_path}")
            if not args.dry_run:
                store.save(o.asset_path, new)
            fixed += 1
    finally:
        db.close()
    print(
        f"done: {fixed} {'to fix' if args.dry_run else 'fixed'}, {ok} already correct, "
        f"{skipped} skipped (unverified up-axis), {errors} errors"
    )
    if skipped_sources:
        print(f"  skipped sources needing manual up-axis check: {sorted(skipped_sources)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
