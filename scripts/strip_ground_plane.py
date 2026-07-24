#!/usr/bin/env python3
"""Strip stray scenery ground/floor planes from existing commissioned GLBs.

Background
----------
Some code-gen models (grok-4.20 systematically; a few others as one-offs) added
a large horizontal ``Plane``/``Soil`` the organism sits on, despite the prompt
asking for "ONE whole specimen — not a scene". That floor is not the organism:
it renders as a slab through the subject in the turntable that both human voters
and the VLM judge score. The commission runner now strips it at generation time
(``app.commission.run_bpy`` -> ``app.mesh_subject``); this script removes it from
GLBs generated before that fix.

It is the ground-plane analogue of ``strip_default_cube.py`` and reuses the same
classifier the runner uses, so on-disk files and future outputs stay consistent.

Safety
------
* Dry-run by default. Pass ``--apply`` to write.
* Every modified file is copied to ``--backup-dir`` first.
* Atomic (temp + ``os.replace``) and verified, so a concurrent reader (a running
  server) never sees a truncated GLB and a bad rewrite never lands.
* Conservative: only a scenery-NAMED, flat, horizontal plane whose footprint is
  at least as large as the biggest real organism mesh is removed; a scene made
  entirely of planes (no organism reference) is left untouched for manual review.
* Idempotent: a clean file is skipped, so re-running is safe.

Usage
-----
    python scripts/strip_ground_plane.py                     # dry run, default dir
    python scripts/strip_ground_plane.py --apply             # write, with backups
    python scripts/strip_ground_plane.py --dir path --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mesh_subject  # noqa: E402

DEFAULT_DIR = Path("data/assets/commissioned")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="directory of GLBs to scan")
    ap.add_argument(
        "--backup-dir", type=Path, default=Path("data/_backups/commissioned_ground_plane_strip")
    )
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args(argv)

    glbs = sorted(args.dir.glob("*.glb"))
    if not glbs:
        print(f"no GLBs under {args.dir}", file=sys.stderr)
        return 1

    counts = {"stripped": 0, "would_strip": 0, "skip_no_scenery": 0}
    for path in glbs:
        res = mesh_subject.strip_scenery_from_glb(
            path, apply=args.apply, backup_dir=args.backup_dir if args.apply else None
        )
        counts[res["action"]] = counts.get(res["action"], 0) + 1
        if res["action"] in ("stripped", "would_strip"):
            print(f"  {res['action']:11} {res['file']}  strip={res['stripped']} kept={res['kept']}")

    mode = "APPLIED" if args.apply else "DRY RUN (pass --apply to write)"
    print(f"\n{mode}")
    print(f"  stripped/would-strip : {counts['stripped'] + counts['would_strip']}")
    print(f"  skipped (no scenery) : {counts['skip_no_scenery']}")
    if args.apply:
        print(f"  backups              : {args.backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
