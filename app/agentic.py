"""Agentic 3D paradigm: an LLM iteratively refines a Blender-authored plant mesh via visual
feedback (render -> critique -> revise), reusing the commission harness. Distinct from
procedural_llm (one-shot). Outputs are ModelOutput(source="agentic:<model>")."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

# Trusted (our own) headless render: import the GLB at IN_GLB, frame all mesh geometry with a
# 3/4 camera + sun over a dark world, render a 512² PNG to OUT_PNG. Uses Blender's default engine.
RENDER_SCRIPT = r"""
import bpy, os, math, mathutils
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=os.environ["IN_GLB"])
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not objs:
    raise SystemExit("no mesh in glb")
mn = mathutils.Vector((1e18, 1e18, 1e18)); mx = mathutils.Vector((-1e18, -1e18, -1e18))
for o in objs:
    for c in o.bound_box:
        w = o.matrix_world @ mathutils.Vector(c)
        mn = mathutils.Vector((min(mn[i], w[i]) for i in range(3)))
        mx = mathutils.Vector((max(mx[i], w[i]) for i in range(3)))
center = (mn + mx) / 2
radius = max((mx - mn)) / 2 or 1.0
cam_d = bpy.data.cameras.new("c"); cam = bpy.data.objects.new("c", cam_d)
bpy.context.scene.collection.objects.link(cam)
d = radius * 3.0
cam.location = center + mathutils.Vector((d * 0.8, -d * 0.8, d * 0.6))
cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
bpy.context.scene.camera = cam
sd = bpy.data.lights.new("s", type="SUN"); sd.energy = 3.0
s = bpy.data.objects.new("s", sd); bpy.context.scene.collection.objects.link(s)
s.rotation_euler = (math.radians(50), 0, math.radians(30))
scn = bpy.context.scene
scn.render.resolution_x = 512; scn.render.resolution_y = 512
scn.render.image_settings.file_format = "PNG"
scn.render.filepath = os.environ["OUT_PNG"]
bpy.ops.render.render(write_still=True)
"""


def _render_env(in_glb: str, out_png: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}
    env["IN_GLB"] = in_glb
    env["OUT_PNG"] = out_png
    return env


def render_glb_png(glb_path, *, blender_bin: str = "blender", timeout_s: int = 120) -> bytes:
    """Headless-Blender render of a GLB to PNG bytes (3/4 view, 512²). Raises RuntimeError on
    any failure (non-zero exit, missing/empty output)."""
    glb_path = str(glb_path)
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "render.py"
        script.write_text(RENDER_SCRIPT)
        out_png = Path(td) / "out.png"
        cmd = [blender_bin, "--background", "--python", str(script)]
        proc = subprocess.run(
            cmd,
            env=_render_env(glb_path, str(out_png)),
            cwd=td,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if proc.returncode != 0 or not out_png.exists() or out_png.stat().st_size == 0:
            raise RuntimeError(f"render failed: rc={proc.returncode} {proc.stderr[-500:]}")
        return out_png.read_bytes()
