"""Subject isolation: drop a stray scenery ground/floor plane an LLM-authored
Blender script leaves in a commissioned scene.

Background
----------
The commission prompt tells each model to "model exactly ONE whole specimen —
not a cluster, not a scene, not a detached part". Some code-gen models ignore
it and add a large horizontal ``Plane``/``Soil`` the organism sits on. That
floor is not the organism: it renders as a slab through the subject in the
turntable that BOTH human voters and the VLM judge score, so it corrupts
fidelity/completeness and Bradley-Terry. It is the ground-plane analogue of the
default-startup-cube artefact (``strip_default_cube``), but the source is
LLM-authored content, not a Blender default — so the empty-scene runner fix does
not catch it.

What counts as a floor (deliberately conservative — over-stripping real anatomy
is worse than leaving a floor)
------------------------------------------------------------------------------
Everything is judged in WORLD space (node transforms applied), because a model
routinely builds a petal/gill/leaf from a ``Plane`` primitive and then rotates
it into place — flat in its own local frame, but oriented anatomy in the scene.

A geometry is stripped only when ALL hold:

1. **Named** like scenery: ``Plane`` / ``Ground`` / ``Soil`` / ``Floor`` /
   ``Terrain`` / ``Backdrop`` / ``Grass`` / ``Dirt`` (optionally ``.NNN``).
   Real anatomy (``Wing_Fore_L``, ``Rose_Petal_Mesh``, ``CaudalFin``) is never a
   candidate, however flat.
2. **Flat & horizontal in world space**: vertical (Y, glTF up) is the thinnest
   axis and under 10% of the larger horizontal extent; few faces (a floor is a
   simple quad, not a detailed organ).
3. **Spans the scene**: its world XZ footprint is >= half the whole scene's
   world XZ footprint — a floor is as wide as everything; an organism part is a
   fraction.
4. **Few candidates**: at most two such planes exist. Anatomy built from planes
   (a rosette of flat leaves, a fan of gills) shows up as *many* flat planes —
   those are left untouched for manual review, never auto-stripped.

Plus the hard invariant: never strip when it would remove the whole scene.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import numpy as np
import trimesh

_SCENERY_NAME = re.compile(
    r"^(plane|ground|soil|floor|terrain|backdrop|grass|dirt)(\.\d+)?$", re.IGNORECASE
)

# A floor is a simple quad/grid, not a detailed organ.
_FACE_CAP = 200
# Vertical extent must be under this fraction of the horizontal size to be a slab.
_FLAT_RATIO = 0.10
# A floor spans at least this fraction of the whole scene's horizontal footprint.
_MIN_SCENE_FRACTION = 0.5
# More flat scenery-named planes than this is anatomy (rosette/gills), not a floor.
_MAX_CANDIDATE_PLANES = 2


def _world_bounds_by_geom(scene) -> dict:
    """geometry name -> (min_xyz, max_xyz) world-space AABB, unioned over every
    node that references the geometry (transforms applied)."""
    out: dict = {}
    for node in scene.graph.nodes_geometry:
        transform, gname = scene.graph[node]
        geom = scene.geometry[gname]
        corners = trimesh.transformations.transform_points(geom.bounding_box.vertices, transform)
        lo, hi = corners.min(axis=0), corners.max(axis=0)
        if gname in out:
            plo, phi = out[gname]
            out[gname] = (np.minimum(plo, lo), np.maximum(phi, hi))
        else:
            out[gname] = (lo, hi)
    return out


def _footprint(bounds) -> float:
    """Horizontal (X*Z) footprint area of a world AABB (lo, hi)."""
    lo, hi = bounds
    return float(hi[0] - lo[0]) * float(hi[2] - lo[2])


def _is_flat_horizontal(bounds) -> bool:
    """True iff the world AABB is a thin horizontal slab: Y is the smallest axis
    and under _FLAT_RATIO of the larger horizontal extent."""
    lo, hi = bounds
    x, y, z = float(hi[0] - lo[0]), float(hi[1] - lo[1]), float(hi[2] - lo[2])
    horiz = max(x, z)
    if horiz <= 0.0 or min(x, z) <= 0.0:
        return False
    if y > min(x, z):  # vertical must be the thinnest axis
        return False
    return y < _FLAT_RATIO * horiz


def scenery_plane_names(scene) -> list[str]:
    """Names of geometries in ``scene`` that are stray scenery ground/floor
    planes (see module docstring), in scene insertion order.

    Returns ``[]`` when nothing qualifies, when more than two flat scenery
    planes exist (anatomy, not a floor), or when stripping would empty the
    scene.
    """
    world = _world_bounds_by_geom(scene)
    candidates = [
        name
        for name, geom in scene.geometry.items()
        if _SCENERY_NAME.match(name) is not None
        and name in world
        and _is_flat_horizontal(world[name])
        and len(geom.faces) <= _FACE_CAP
    ]
    if not (1 <= len(candidates) <= _MAX_CANDIDATE_PLANES):
        return []

    sb = scene.bounds
    scene_fp = float(sb[1][0] - sb[0][0]) * float(sb[1][2] - sb[0][2])
    if scene_fp <= 0.0:
        return []

    strip = [n for n in candidates if _footprint(world[n]) >= _MIN_SCENE_FRACTION * scene_fp]
    if not strip or len(strip) >= len(scene.geometry):  # never empty the scene
        return []
    return strip


def strip_scenery_from_glb(path, *, apply: bool, backup_dir=None) -> dict:
    """Remove scenery ground/floor planes from one GLB.

    Dry-run unless ``apply``. When applying and ``backup_dir`` is given, the
    original is copied there first. Writes atomically (temp + ``os.replace``)
    and verifies the rewrite kept exactly the intended geometry before it
    replaces the original, so a concurrent reader never sees a truncated GLB and
    a bad rewrite never lands.

    Returns a status dict with ``action`` in
    {``skip_no_scenery``, ``would_strip``, ``stripped``}.
    """
    path = Path(path)
    scene = trimesh.load(str(path), force="scene")
    strip = scenery_plane_names(scene)
    if not strip:
        return {"file": path.name, "action": "skip_no_scenery"}

    kept = [n for n in scene.geometry if n not in strip]
    if not apply:
        return {"file": path.name, "action": "would_strip", "stripped": strip, "kept": len(kept)}

    if backup_dir is not None:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)

    for name in strip:
        scene.delete_geometry(name)

    tmp = path.with_suffix(".glb.tmp")
    scene.export(str(tmp), file_type="glb")

    check = trimesh.load(str(tmp), file_type="glb", force="scene")
    if set(check.geometry) != set(kept):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{path.name}: kept geometry set changed "
            f"(-{set(kept) - set(check.geometry)} +{set(check.geometry) - set(kept)})"
        )

    os.replace(tmp, path)
    return {"file": path.name, "action": "stripped", "stripped": strip, "kept": len(kept)}
