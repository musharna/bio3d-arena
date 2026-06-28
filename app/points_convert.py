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


def stand_up_z(verts: np.ndarray) -> np.ndarray:
    """Rotate a +Z-up cloud into the +Y-up convention <model-viewer> expects.

    (x, y, z) → (x, z, −y) (a −90° rotation about X, Z→Y), then recentre on the bbox
    centre so the plant stands up at the origin instead of lying on its side. The single
    source of the up-axis convention, shared by ingest, GT bake, and the reorient fix.
    """
    verts = np.asarray(verts, dtype=float)
    verts = np.column_stack([verts[:, 0], verts[:, 2], -verts[:, 1]])
    return verts - (verts.min(axis=0) + verts.max(axis=0)) / 2.0


def array_to_glb(
    verts: np.ndarray,
    colors: np.ndarray | None = None,
    *,
    max_points: int = 200_000,
    seed: int = 0,
    up_axis: str | None = None,
) -> bytes:
    """Export an (N,3) vertex array (+optional colours) as a glTF POINTS GLB.

    Shared core for points_to_glb / npy_to_glb / the reorient migration. up_axis="z"
    applies stand_up_z; None keeps coordinates as-is. Clouds above max_points are
    randomly subsampled (fixed seed → reproducible); colours ride along the subsample.
    """
    verts = np.asarray(verts, dtype=float)
    if len(verts) == 0:
        raise PointsConvertError("no vertices, nothing to render")
    if up_axis == "z":
        verts = stand_up_z(verts)

    if colors is not None:
        colors = np.asarray(colors)
        if len(colors) != len(verts):
            colors = None

    if len(verts) > max_points:
        idx = np.random.RandomState(seed).choice(len(verts), size=max_points, replace=False)
        verts = verts[idx]
        if colors is not None:
            colors = colors[idx]

    cloud = trimesh.PointCloud(verts, colors=colors)
    glb = cloud.export(file_type="glb")
    if not glb:
        raise PointsConvertError("empty GLB export")
    return glb


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

    colors = None
    if getattr(loaded, "colors", None) is not None and len(loaded.colors) == len(verts):
        colors = np.asarray(loaded.colors)
    else:
        visual = getattr(loaded, "visual", None)
        vc = getattr(visual, "vertex_colors", None) if visual is not None else None
        if vc is not None and len(vc) == len(verts):
            colors = np.asarray(vc)

    return array_to_glb(verts, colors, max_points=max_points, seed=seed, up_axis=up_axis)


def npy_to_glb(
    src_path: str, *, max_points: int = 200_000, seed: int = 0, up_axis: str | None = None
) -> bytes:
    """Load an (N,3) .npy point cloud (e.g. a scorer GT scan) and export a POINTS GLB.

    GT bundle clouds are plain coordinate arrays with no colour; up_axis="z" stands them
    upright (all bio3d GT bundles are +Z-up). Raises PointsConvertError on a non-(N,3) array.
    """
    arr = np.load(src_path)
    arr = np.asarray(arr, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise PointsConvertError(f"{src_path}: expected (N,3) array, got shape {arr.shape}")
    return array_to_glb(arr, None, max_points=max_points, seed=seed, up_axis=up_axis)
