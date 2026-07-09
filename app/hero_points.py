"""Bake-time helpers for the home-page hero point clouds.

Turns a held-out ground-truth *scan* (a POINTS glb, plants) or a reconstruction *mesh*
(a triangle glb, fungi/animals) into a small, cleaned, unit-normalized point cloud the
hero renders. Imported only by scripts/bake_hero_points.py and the tests — never by the
running app (the baked JSON is committed and served statically, promote-don't-recompute),
so the scipy dependency here never loads at request time.
"""

from __future__ import annotations

import json
import struct

import numpy as np

# glTF componentType -> numpy dtype; accessor type -> component count.
_CT = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _parse_glb(data: bytes) -> tuple[dict, bytes]:
    magic, _ver, _len = struct.unpack("<III", data[:12])
    if magic != 0x46546C67:  # "glTF"
        raise ValueError("not a GLB file")
    clen, _ctype = struct.unpack("<II", data[12:20])
    js = json.loads(data[20 : 20 + clen])
    rest = data[20 + clen :]
    blen, _btype = struct.unpack("<II", rest[:8])
    binb = rest[8 : 8 + blen]
    return js, binb


def _accessor(js: dict, binb: bytes, idx: int) -> np.ndarray:
    a = js["accessors"][idx]
    bv = js["bufferViews"][a["bufferView"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    nc = _NC[a["type"]]
    arr = np.frombuffer(binb, dtype=_CT[a["componentType"]], count=a["count"] * nc, offset=off)
    return arr.reshape(a["count"], nc).astype(np.float64)


def points_arrays(data: bytes) -> np.ndarray:
    """Vertex positions of a POINTS-mode GLB (a baked GT scan)."""
    js, binb = _parse_glb(data)
    out = []
    for m in js.get("meshes", []):
        for pr in m.get("primitives", []):
            if "POSITION" in pr.get("attributes", {}):
                out.append(_accessor(js, binb, pr["attributes"]["POSITION"])[:, :3])
    if not out:
        raise ValueError("no POSITION data in GLB")
    return np.concatenate(out, 0)


def mesh_arrays(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """(positions, triangles) of a triangle-mode GLB (a reconstruction mesh)."""
    js, binb = _parse_glb(data)
    positions, tris, base = [], [], 0
    for m in js.get("meshes", []):
        for pr in m.get("primitives", []):
            if pr.get("mode", 4) != 4 or "POSITION" not in pr.get("attributes", {}):
                continue
            P = _accessor(js, binb, pr["attributes"]["POSITION"])[:, :3]
            if "indices" in pr:
                idx = _accessor(js, binb, pr["indices"]).astype(int).ravel()
            else:
                idx = np.arange(len(P))
            positions.append(P)
            tris.append(idx.reshape(-1, 3) + base)
            base += len(P)
    if not positions:
        raise ValueError("no triangle primitives in GLB")
    return np.concatenate(positions, 0), np.concatenate(tris, 0)


def sample_surface(
    positions: np.ndarray, triangles: np.ndarray, n: int, seed: int = 3
) -> np.ndarray:
    """Area-weighted random points across the mesh surface (even coverage, not just verts)."""
    rng = np.random.default_rng(seed)
    v0, v1, v2 = positions[triangles[:, 0]], positions[triangles[:, 1]], positions[triangles[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)
    total = areas.sum()
    if total <= 0:
        raise ValueError("degenerate mesh (zero surface area)")
    pick = rng.choice(len(triangles), size=n, p=areas / total)
    u, v = rng.random(n), rng.random(n)
    over = u + v > 1
    u[over], v[over] = 1 - u[over], 1 - v[over]
    a, b, c = v0[pick], v1[pick], v2[pick]
    return a + (b - a) * u[:, None] + (c - a) * v[:, None]


def remove_outliers(
    pts: np.ndarray, k: int = 12, ratio: float = 2.5, passes: int = 2
) -> np.ndarray:
    """Statistical outlier removal: drop points whose mean distance to their k nearest
    neighbours exceeds mean + ratio*std. Reconstruction meshes carry stray fragments the
    GT scans don't; this strips them so one far point can't skew the normalization scale."""
    from scipy.spatial import cKDTree

    out = pts
    for _ in range(passes):
        if len(out) <= k + 1:
            break
        tree = cKDTree(out)
        d, _ = tree.query(out, k=k + 1)
        md = d[:, 1:].mean(1)
        keep = md <= (md.mean() + ratio * md.std())
        if keep.all():
            break
        out = out[keep]
    return out


def normalize(pts: np.ndarray) -> np.ndarray:
    """Centre on the bbox midpoint and scale so the largest half-extent is 1."""
    center = (pts.max(0) + pts.min(0)) / 2
    centered = pts - center
    scale = float(np.abs(centered).max()) or 1.0
    return centered / scale


def prepare_cloud(
    positions: np.ndarray,
    triangles: np.ndarray | None,
    n: int = 6000,
    target: int = 5000,
    seed: int = 3,
    decimals: int = 3,
) -> list[list[float]]:
    """Full pipeline -> JSON-ready [[x,y,z], ...]. `triangles=None` means `positions` is
    already a point cloud (a GT scan); otherwise surface-sample the mesh first."""
    pts = positions if triangles is None else sample_surface(positions, triangles, n, seed)
    pts = remove_outliers(pts)
    if len(pts) > target:
        rng = np.random.default_rng(seed + 1)
        pts = pts[rng.choice(len(pts), target, replace=False)]
    pts = np.round(normalize(pts), decimals)
    return [[float(x), float(y), float(z)] for x, y, z in pts]
