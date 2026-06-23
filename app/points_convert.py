# app/points_convert.py
"""Convert a scan-dataset point cloud (.ply/.pcd/.xyz, or a mesh used as points) to a
glTF POINTS GLB for <model-viewer>.

The mirror of app/mesh_convert.py, which REJECTS point clouds. Here we embrace them:
the points ARE the data, so we render them faithfully (no surface reconstruction).
trimesh.PointCloud exports primitive mode 0 (POINTS), which model-viewer's three.js
core renders. trimesh + numpy are already dependencies.
"""

from __future__ import annotations

import numpy as np
import trimesh


class PointsConvertError(Exception):
    """Raised when an asset has no usable vertices to render as a point cloud."""


def points_to_glb(
    src_path: str, *, max_points: int = 200_000, seed: int = 0, up_axis: str | None = None
) -> bytes:
    """Load a point-cloud asset and export a glTF POINTS GLB.

    Raises PointsConvertError if the asset has no vertices. A mesh source is accepted —
    its vertices become the point set (still faithful to the scan). Clouds larger than
    max_points are randomly subsampled (fixed seed → reproducible) so the GLB stays
    web-renderable; vertex colours, when present, are preserved through the subsample.

    up_axis: source up-axis. Default None keeps coordinates as-is. "z" handles +Z-up scans
    (e.g. Crops3D field LiDAR) by rotating −90° about X (Z→Y) and recentring, so the plant
    stands up in the +Y-up <model-viewer> instead of lying on its side.
    """
    loaded = trimesh.load(src_path)  # NOT force="mesh" — keep the cloud
    verts = getattr(loaded, "vertices", None)
    if verts is None or len(verts) == 0:
        raise PointsConvertError(f"{src_path}: no vertices, nothing to render")
    verts = np.asarray(verts, dtype=float)
    if up_axis == "z":  # (x, y, z) → (x, z, −y); then recentre on the bbox centre
        verts = np.column_stack([verts[:, 0], verts[:, 2], -verts[:, 1]])
        verts = verts - (verts.min(axis=0) + verts.max(axis=0)) / 2.0

    colors = None
    if getattr(loaded, "colors", None) is not None and len(loaded.colors) == len(verts):
        colors = np.asarray(loaded.colors)
    else:
        visual = getattr(loaded, "visual", None)
        vc = getattr(visual, "vertex_colors", None) if visual is not None else None
        if vc is not None and len(vc) == len(verts):
            colors = np.asarray(vc)

    if len(verts) > max_points:
        idx = np.random.RandomState(seed).choice(len(verts), size=max_points, replace=False)
        verts = verts[idx]
        if colors is not None:
            colors = colors[idx]

    cloud = trimesh.PointCloud(verts, colors=colors)
    glb = cloud.export(file_type="glb")
    if not glb:
        raise PointsConvertError(f"{src_path}: empty GLB export")
    return glb
