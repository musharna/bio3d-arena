from __future__ import annotations

import shutil

import pytest
import trimesh

from app import commission


def test_is_valid_mesh_true_for_real_glb(tmp_path):
    p = tmp_path / "box.glb"
    trimesh.creation.box().export(str(p))
    ok, stats = commission.is_valid_mesh(p)
    assert ok is True
    assert stats["vertices"] > 0 and stats["faces"] > 0


def test_is_valid_mesh_false_for_empty_or_missing(tmp_path):
    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")
    assert commission.is_valid_mesh(empty)[0] is False
    assert commission.is_valid_mesh(tmp_path / "nope.glb")[0] is False


_KNOWN_GOOD_BPY = """
import bpy, os
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add()
bpy.ops.export_scene.gltf(filepath=os.environ['OUT_GLB'], export_format='GLB')
"""


@pytest.mark.skipif(shutil.which("blender") is None, reason="blender not installed")
def test_run_bpy_known_good_script_produces_valid_glb(tmp_path):
    out = tmp_path / "out.glb"
    res = commission.run_bpy(_KNOWN_GOOD_BPY, out_glb=out, timeout_s=120)
    assert res["status"] == "ok"
    assert res["glb_path"] and commission.is_valid_mesh(out)[0] is True


def test_run_bpy_missing_blender_returns_error(tmp_path):
    res = commission.run_bpy(
        "print('x')", out_glb=tmp_path / "o.glb", blender_bin="definitely-not-blender"
    )
    assert res["status"] == "error" and res["glb_path"] is None
