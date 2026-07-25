#!/usr/bin/env python3
"""Strip display plinths/pedestals from six human-verified LLM-authored GLBs.

Background
----------
``strip_ground_plane.py`` removes stray FLOORS: thin, horizontal, scenery-named
quads. A live arena audit (2026-07-25) surfaced a second, distinct artefact —
the organism standing on a chunky display PLINTH. It defeats
``app.mesh_subject.scenery_plane_names`` on three independent axes at once:

===============  ==================================  ==========================
axis             classifier expects                  these outputs
===============  ==================================  ==========================
name             ``plane|ground|soil|floor|…``       ``Cube.001``, ``Cylinder``
face count       ``<= _FACE_CAP`` (200)              60 - 2944
thickness        ``< _FLAT_RATIO`` (0.10) of span    0.02 - 0.32
===============  ==================================  ==========================

Why this is a NAMED list and not a widened classifier
-----------------------------------------------------
Loosening the classifier far enough to catch these also catches real anatomy.
The audit that produced this list ran exactly that experiment: a footprint +
thickness rule flagged output #800's ``Cube`` — which is the DOG'S TORSO — and
cleared #716, whose plinth is the worst offender in the corpus. A geometric
rule cannot separate "pedestal" from "blocky body" here, so a human reviewed a
rendered contact sheet of all 15 candidates and named the six that are real.
Being wrong in the stripping direction destroys an organism, so the conservative
classifier stays as-is and this script carries the verified exceptions.

Note that ``substrate`` is a legitimate optional organ for *Trametes versicolor*
and *Hericium erinaceus* (see ``app.organ_inventory``), which is why three
turkey-tail-on-wood outputs are deliberately NOT in this list — the wood is
correct biology. #716 is a Hericium and IS listed: its render is a small dome on
a smooth tan plinth 2.5x its width, not a piece of bark.

Safety
------
* Dry-run by default; pass ``--apply`` to write.
* Backs up every modified file, writes atomically, and verifies the reloaded
  geometry set before swapping in (see ``mesh_subject._rewrite_without``).
* Idempotent: an already-stripped file reports ``skip_absent``, so a partial run
  is safe to resume.
* Never empties a scene, whatever this list says.

Usage
-----
    python scripts/strip_pedestal.py             # dry run
    python scripts/strip_pedestal.py --apply     # write, with backups
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mesh_subject  # noqa: E402

# (output id, asset path, geometry to strip, what the reviewer saw).
# Verified by eye on a rendered contact sheet — do not extend without doing the
# same, and never from geometry heuristics alone (see module docstring).
PEDESTALS: list[tuple[int, str, str, str]] = [
    (
        716,
        "commissioned/openrouter-z-ai-glm-4-6v_23.glb",
        "Cube.001",
        "Hericium: small white dome on a smooth tan plinth 2.5x its width",
    ),
    (
        913,
        "agentic/agentic-openai-gpt-5-6-sol_12.glb",
        "Cylinder",
        "Zea mays standing on a soil disc",
    ),
    (
        936,
        "agentic/agentic-mistralai-mistral-medium-3-5_20.glb",
        "Cylinder.004",
        "Glycine max on a raised disc",
    ),
    (
        941,
        "agentic/agentic-openai-gpt-5-6-sol_10.glb",
        "Sphere",
        "Arabidopsis rosette on a soil dome",
    ),
    (
        943,
        "agentic/agentic-qwen-qwen3-7-plus_10.glb",
        "Cylinder",
        "Arabidopsis rosette on a tan disc",
    ),
    (
        1025,
        "agentic/agentic-openai-gpt-5-6-sol_29.glb",
        "Cylinder.002",
        "Anas platyrhynchos on a grey disc",
    ),
]

DEFAULT_ASSETS = Path("data/assets")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS)
    ap.add_argument("--backup-dir", type=Path, default=Path("data/_backups/pedestal_strip"))
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args(argv)

    counts: dict[str, int] = {}
    missing = 0
    for oid, rel, geom, note in PEDESTALS:
        path = args.assets_dir / rel
        if not path.exists():
            print(f"  MISSING     #{oid} {rel}", file=sys.stderr)
            missing += 1
            continue
        res = mesh_subject.strip_named_geometry_from_glb(
            path, [geom], apply=args.apply, backup_dir=args.backup_dir if args.apply else None
        )
        counts[res["action"]] = counts.get(res["action"], 0) + 1
        kept = res.get("kept", "-")
        print(f"  {res['action']:16} #{oid:5} {geom:14} kept={kept:3}  {note}")

    print(f"\n{'APPLIED' if args.apply else 'DRY RUN (pass --apply to write)'}")
    for action, n in sorted(counts.items()):
        print(f"  {action:16} {n}")
    if missing:
        print(f"  missing on disk  {missing}", file=sys.stderr)
    if args.apply:
        print(f"  backups          {args.backup_dir}")
    # Fail loud if an asset named in the verified list is not where it should be.
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
