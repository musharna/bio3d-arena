"""Tests for the reference-scan reorientation fix (raw +Z-up → +Y-up, idempotent)."""

from __future__ import annotations

import numpy as np
import trimesh

from app.points_convert import array_to_glb
from app.reorient import needs_reorient, reorient_glb_bytes
from app.sourcing import is_z_up_scan


def _raw_z_up_cloud():
    """A +Z-up cloud (clearly tall in Z), offset from the origin like a never-reoriented scan."""
    return np.random.RandomState(0).rand(800, 3) * np.array([20, 15, 60]) + np.array([44, 59, 9])


def _verts_of(glb: bytes) -> np.ndarray:
    rt = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb")
    geom = rt if hasattr(rt, "vertices") else rt.geometry[next(iter(rt.geometry))]
    return np.asarray(geom.vertices)


def test_needs_reorient_true_for_offset_cloud():
    assert needs_reorient(_raw_z_up_cloud())


def test_needs_reorient_false_for_recentred_cloud():
    centred = _raw_z_up_cloud()
    centred = centred - (centred.min(0) + centred.max(0)) / 2.0
    assert not needs_reorient(centred)


def test_reorient_fixes_raw_glb_to_y_up_recentred():
    raw_glb = array_to_glb(_raw_z_up_cloud(), up_axis=None)  # baked as-is (the bug)
    fixed = reorient_glb_bytes(raw_glb)
    assert fixed is not None
    v = _verts_of(fixed)
    ext = v.max(0) - v.min(0)
    assert ext[1] == max(ext)  # tallest axis is now Y
    assert np.allclose((v.max(0) + v.min(0)) / 2.0, 0, atol=1e-4)  # recentred


def test_reorient_is_noop_on_already_correct_glb():
    correct = array_to_glb(_raw_z_up_cloud(), up_axis="z")  # already stood up + recentred
    assert reorient_glb_bytes(correct) is None  # no double-rotation


def test_reorient_idempotent():
    """Re-orienting twice equals once (second pass is a no-op)."""
    raw_glb = array_to_glb(_raw_z_up_cloud(), up_axis=None)
    once = reorient_glb_bytes(raw_glb)
    assert once is not None
    assert reorient_glb_bytes(once) is None


def test_reorient_preserves_colors():
    verts = _raw_z_up_cloud()
    colors = np.tile(np.array([[10, 200, 30, 255]], dtype=np.uint8), (len(verts), 1))
    raw_glb = array_to_glb(verts, colors, up_axis=None)
    fixed = reorient_glb_bytes(raw_glb)
    assert fixed is not None
    rt = trimesh.load(trimesh.util.wrap_as_stream(fixed), file_type="glb")
    geom = rt if hasattr(rt, "colors") else rt.geometry[next(iter(rt.geometry))]
    mean = np.asarray(geom.colors)[:, :3].mean(axis=0)
    assert mean[1] > mean[0] and mean[1] > mean[2]  # green survives


def test_is_z_up_scan_allowlist():
    # Verified +Z-up sources are fixable.
    assert is_z_up_scan("rose-x")
    assert is_z_up_scan("ct:rose-x")
    assert is_z_up_scan("romi-arabidopsis")
    assert is_z_up_scan("mri:ipk-barley-mri")
    assert is_z_up_scan("crops3d")
    assert is_z_up_scan("plant3d")
    # icrisat-legume is +Z-up too (flat broad-leaf canopy: thin in z, wide in x-y).
    assert is_z_up_scan("icrisat-legume")
    assert not is_z_up_scan(None)
    assert not is_z_up_scan("api:fal:trellis")
