#!/usr/bin/env python3
"""Strip Blender's leftover default startup cube from agentic GLB outputs.

Background
----------
The agentic/procedural generation runner used to exec a model's Blender-Python
script against Blender's *factory startup scene*, which ships a default ``Cube``
(2×2×2 at the origin, plus a camera and light). A model script that built its
organism without first clearing the scene therefore exported that stray Cube
alongside the plant — a harness artefact, not the model's work. It inflates the
mesh bounds (so the viewer shrinks the real organism to frame the box) and shows
up as a literal box in the 3D view. The runner was fixed to open an empty scene
(``read_factory_settings(use_empty=True)``, matching the render path), but GLBs
generated *before* that fix still carry the cube. This script removes it from
those existing files, preserving the model's actual geometry and materials.

Two populations exist and are handled differently:

* **plant + cube** — the model built a real organism and the stray cube rode
  along. We delete the cube geometry and re-export; the organism is untouched.
* **cube only** — the model built *nothing* valid and the export captured just
  the leftover cube (which passes ``is_valid_mesh`` because a cube is a valid
  mesh). Stripping would leave an empty file, so these are NOT stripped here —
  they are reported for separate disposition (they represent generation
  failures scored as valid outputs, a benchmark-integrity decision).

Safety
------
* Dry-run by default. Pass ``--apply`` to write.
* Every modified file is copied to ``--backup-dir`` first.
* Writes atomically (temp file + ``os.replace``) so a concurrent reader (e.g. a
  running server) never sees a truncated GLB.
* Idempotent: a file with no default cube is skipped, so re-running is safe.

Usage
-----
    python scripts/strip_default_cube.py                    # dry run, default dir
    python scripts/strip_default_cube.py --apply            # write, with backups
    python scripts/strip_default_cube.py --dir path/to/glbs --apply
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import trimesh

DEFAULT_DIR = Path("data/assets/agentic")
_CUBE_NAME = re.compile(r"^Cube(\.\d+)?$")


def is_startup_cube(name: str, geom) -> bool:
    """True iff ``geom`` is Blender's default startup cube: 12 triangles, a
    ~2×2×2 axis-aligned box centred at the origin, named ``Cube`` (or
    ``Cube.NNN``). Vertex count is 8 (merged) or 24 (glTF split-normal export),
    so it is deliberately not part of the signature. The name + exact default
    dimensions make a false positive (a model's intentional 2×2×2 box named
    exactly "Cube" at the origin) vanishingly unlikely — a real organ never
    dwarfs the whole plant with a unit cube at the origin."""
    if geom.faces.shape[0] != 12:
        return False
    if not _CUBE_NAME.match(name):
        return False
    b = geom.bounds
    ext = b[1] - b[0]
    center = (b[0] + b[1]) / 2
    return bool(
        np.allclose(ext, [2, 2, 2], atol=0.05) and np.allclose(center, [0, 0, 0], atol=0.05)
    )


def classify(scene) -> tuple[list[str], list[str]]:
    """Return (cube_names, plant_names) for a loaded scene."""
    cubes, plants = [], []
    for name, geom in scene.geometry.items():
        (cubes if is_startup_cube(name, geom) else plants).append(name)
    return cubes, plants


def strip_file(path: Path, backup_dir: Path, *, apply: bool) -> dict:
    """Strip the default cube from one GLB. Returns a status dict; only writes
    when ``apply`` is True. Never modifies a cube-only file."""
    scene = trimesh.load(str(path), force="scene")
    cubes, plants = classify(scene)
    if not cubes:
        return {"file": path.name, "action": "skip_no_cube"}
    if not plants:
        return {"file": path.name, "action": "skip_cube_only", "cubes": cubes}

    if not apply:
        return {"file": path.name, "action": "would_strip", "cubes": cubes, "plants": len(plants)}

    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / path.name)

    for name in cubes:
        scene.delete_geometry(name)

    tmp = path.with_suffix(".glb.tmp")
    scene.export(str(tmp), file_type="glb")

    # Verify the rewrite before it replaces the original.
    check = trimesh.load(str(tmp), file_type="glb", force="scene")
    check_cubes, check_plants = classify(check)
    if check_cubes:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{path.name}: cube survived the strip ({check_cubes})")
    if set(check_plants) != set(plants):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{path.name}: plant geometry set changed "
            f"(-{set(plants) - set(check_plants)} +{set(check_plants) - set(plants)})"
        )

    os.replace(tmp, path)
    return {"file": path.name, "action": "stripped", "cubes": cubes, "plants": len(plants)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="directory of GLBs to scan")
    ap.add_argument("--backup-dir", type=Path, default=Path("data/_backups/agentic_cube_strip"))
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args(argv)

    glbs = sorted(args.dir.glob("*.glb"))
    if not glbs:
        print(f"no GLBs under {args.dir}", file=sys.stderr)
        return 1

    counts = {"stripped": 0, "would_strip": 0, "skip_no_cube": 0, "skip_cube_only": 0}
    cube_only = []
    for path in glbs:
        res = strip_file(path, args.backup_dir, apply=args.apply)
        counts[res["action"]] = counts.get(res["action"], 0) + 1
        if res["action"] == "skip_cube_only":
            cube_only.append(res["file"])
        if res["action"] in ("stripped", "would_strip"):
            print(
                f"  {res['action']:11} {res['file']}  cubes={res['cubes']} plants={res['plants']}"
            )

    mode = "APPLIED" if args.apply else "DRY RUN (pass --apply to write)"
    print(f"\n{mode}")
    print(f"  stripped/would-strip : {counts['stripped'] + counts['would_strip']}")
    print(f"  skipped (no cube)    : {counts['skip_no_cube']}")
    print(f"  skipped (cube-only)  : {counts['skip_cube_only']}  {cube_only}")
    if args.apply:
        print(f"  backups              : {args.backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
