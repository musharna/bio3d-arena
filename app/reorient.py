# app/reorient.py
"""Re-orient point-cloud GLBs that were ingested without the +Z-up → +Y-up rotation.

Scans ingested via `source_scans.py --up-axis none` keep their native +Z-up coordinates
(not recentred), so they render lying down / upside-down in the +Y-up <model-viewer>.
Scans ingested with `--up-axis z` were rotated AND recentred to the origin. That gives a
reliable, idempotent discriminator: a cloud whose bbox centre is far from the origin was
never reoriented. `reorient_glb_bytes` fixes those in place and is a no-op on the rest, so
re-running the migration can never double-rotate a correct cloud.
"""

from __future__ import annotations

import numpy as np
import trimesh

from .points_convert import array_to_glb

# A reoriented cloud is recentred (centre ≈ 0). Flag as "raw" only when the centre is
# displaced by more than this fraction of the largest extent — well above float noise,
# well below the ~0.5 ratio a never-centred scan shows.
_OFFSET_RATIO = 0.05


def _scene_points(glb_bytes: bytes) -> tuple[np.ndarray, np.ndarray | None]:
    """Concatenate vertices (and colours, if uniform-length) from a GLB point cloud."""
    obj = trimesh.load(trimesh.util.wrap_as_stream(glb_bytes), file_type="glb")
    geoms = list(obj.geometry.values()) if isinstance(obj, trimesh.Scene) else [obj]
    vparts, cparts = [], []
    for g in geoms:
        v = np.asarray(getattr(g, "vertices", np.empty((0, 3))), dtype=float)
        if len(v) == 0:
            continue
        vparts.append(v)
        c = getattr(g, "colors", None)
        cparts.append(np.asarray(c) if c is not None and len(c) == len(v) else None)
    if not vparts:
        return np.empty((0, 3)), None
    verts = np.vstack(vparts)
    colors = np.vstack(cparts) if all(c is not None for c in cparts) else None
    return verts, colors


def needs_reorient(verts: np.ndarray) -> bool:
    """True if the cloud was never recentred (its bbox centre is offset from the origin)."""
    if len(verts) == 0:
        return False
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    center = (lo + hi) / 2.0
    extent = float((hi - lo).max())
    if extent == 0:
        return False
    return float(np.abs(center).max()) > _OFFSET_RATIO * extent


def reorient_glb_bytes(glb_bytes: bytes) -> bytes | None:
    """Return a +Y-up, recentred GLB if the input was raw +Z-up; else None (no change needed)."""
    verts, colors = _scene_points(glb_bytes)
    if not needs_reorient(verts):
        return None
    # max_points high enough to never re-subsample an already-capped ingest cloud.
    return array_to_glb(verts, colors, up_axis="z", max_points=len(verts))
