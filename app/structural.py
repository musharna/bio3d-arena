# app/structural.py
"""Structural-validity predicate: pure trimesh geometry, no VLM. Rejects ONLY unambiguous
degeneracy (conservative / precision-first) — a false positive silently removes a real candidate,
so thresholds are tuned to reject the flagged broken set with ZERO false positives on good meshes."""

from __future__ import annotations

import numpy as np

from .admissibility import Verdict

VERSION = "structural-v1"

# Conservative floors. A real 3D plant mesh has thousands of verts/faces and true 3D extent;
# a degenerate output (single triangle, flat sheet, empty/corrupt) fails one of these.
MIN_VERTS = 8
MIN_FACES = 8
MIN_EXTENT_RATIO = 0.02  # smallest bbox extent / bbox diagonal; below this = a sliver/flat


def evaluate_glb(path: str) -> Verdict:
    """Load a GLB (concatenated to one mesh) and return an admissibility Verdict."""
    import trimesh  # local import: heavy

    try:
        mesh = trimesh.load(path, force="mesh")  # repo idiom (ingest._validate_mesh)
    except Exception as e:  # noqa: BLE001 — a corrupt asset is a reject, not a crash
        return Verdict(False, "unreadable", {"error": str(e)[:200]})

    verts = np.asarray(getattr(mesh, "vertices", np.empty((0, 3))), dtype=float)
    faces = getattr(mesh, "faces", None)
    nv = int(len(verts))
    nf = 0 if faces is None else int(len(faces))

    if nv == 0 or nf == 0:
        return Verdict(False, "empty", {"verts": nv, "faces": nf})
    if not np.isfinite(verts).all():
        return Verdict(False, "non_finite", {})
    if nv < MIN_VERTS or nf < MIN_FACES:
        return Verdict(False, "too_small", {"verts": nv, "faces": nf})

    extents = np.asarray(mesh.extents, dtype=float)  # bbox size (3,)
    diag = float(np.linalg.norm(extents))
    ratio = float(extents.min() / diag) if diag > 0 else 0.0
    if ratio < MIN_EXTENT_RATIO:
        return Verdict(False, "degenerate_bbox", {"extent_ratio": ratio})

    return Verdict(True, "", {"verts": nv, "faces": nf, "extent_ratio": ratio})
