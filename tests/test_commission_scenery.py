"""run_bpy must drop a stray scenery ground plane the model left in the scene
BEFORE the mesh is validated and ingested — the causal fix for the
ground-plane artefact (grok-4.20 et al.). Real Blender run: pairs the synthetic
unit tests in test_mesh_subject with a live end-to-end check.
"""

from __future__ import annotations

import shutil

import pytest
import trimesh

from app import commission, mesh_subject

# A model script that builds a real organism (sphere) AND leaves a big floor
# plane behind — exactly the grok-4.20 Boletus shape. RUNNER_SRC owns the
# export, so the script itself writes nothing.
_SPHERE_PLUS_FLOOR = """
import bpy
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.35)
bpy.ops.mesh.primitive_plane_add(size=6)
"""


@pytest.mark.skipif(shutil.which("blender") is None, reason="blender not installed")
def test_run_bpy_strips_a_stray_floor_plane_before_validation(tmp_path):
    out = tmp_path / "sphere_plus_floor.glb"
    res = commission.run_bpy(_SPHERE_PLUS_FLOOR, out_glb=out, timeout_s=120)

    assert res["status"] == "ok"
    scene = trimesh.load(str(out), force="scene")
    # the floor is gone, the organism survives
    assert mesh_subject.scenery_plane_names(scene) == []
    assert len(scene.geometry) >= 1
    assert not any(name.lower().startswith("plane") for name in scene.geometry)
