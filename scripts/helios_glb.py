"""Convert a Helios tomato OBJ to a GLB with the alpha-cutout leaf texture restored.

Helios' `Context::writeOBJ` drops leaf textures on export (a textured `addTile` writes 0 UVs +
`Kd 0 0 0` + no `map_Kd`), so a plain trimesh OBJ->GLB yields flat black leaf cards. This module
runs Blender headless to (a) give each one-quad leaf a unit-square UV, (b) bind the leaf texture's
RGB to base color and its alpha to the BSDF for an alpha-cutout silhouette, then patches the GLB so
the leaf material uses `alphaMode=MASK` (robust foliage cutout in model-viewer; no depth sorting).

`force_mask_alpha` is pure-python (no Blender) and unit-tested; the Blender pass is real-execution
tooling, gated on a Blender binary being present (mirrors the Helios build-gate's real-exec note).
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

DEFAULT_BLENDER = os.environ.get("BLENDER_BIN", str(Path.home() / "blender/blender"))
DEFAULT_LEAF_TEX = os.environ.get(
    "HELIOS_LEAF_TEXTURE",
    str(Path.home() / "Helios/plugins/canopygenerator/textures/TomatoLeaf_big.png"),
)

# bpy script: import OBJ, apply the alpha-cutout leaf texture to the black (texture-driven) leaf
# material, keep the colored fruit/stem materials, export GLB. Args after `--`: <src.obj> <out.glb>
# <leaf_texture.png>.
_BLENDER_SCRIPT = r"""
import sys, bpy
argv = sys.argv[sys.argv.index("--") + 1:]
src, out, leaf_tex = argv[0], argv[1], argv[2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=src)
obj = next(o for o in bpy.data.objects if o.type == "MESH")
bpy.context.view_layer.objects.active = obj
img = bpy.data.images.load(leaf_tex)
for slot_i, slot in enumerate(obj.material_slots):
    m = slot.material
    if not m or not m.use_nodes:
        continue
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        continue
    b = bsdf.inputs["Base Color"].default_value
    is_leaf = (b[0] < 0.1 and b[1] < 0.1 and b[2] < 0.1)  # texture-driven leaf exports as black
    if not is_leaf:
        continue
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    obj.active_material_index = slot_i
    bpy.ops.object.material_slot_select()
    bpy.ops.uv.reset()  # each one-quad leaf -> unit-square UV (full leaf texture per leaf)
    bpy.ops.object.mode_set(mode="OBJECT")
    nt = m.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
    m.blend_method = "CLIP"
    m.alpha_threshold = 0.5
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", use_selection=False,
                          export_apply=True, export_yup=True)
"""


def force_mask_alpha(glb_bytes: bytes, cutoff: float = 0.4) -> bytes:
    """Rewrite a binary GLB so every textured material uses alphaMode=MASK (+ doubleSided).

    Blender's glTF exporter writes BLEND for a clip material on some versions; MASK is the robust
    choice for billboard foliage. Materials without a base-color texture are left untouched.
    """
    magic, ver, _length = struct.unpack("<III", glb_bytes[:12])
    if magic != 0x46546C67:  # 'glTF' — fail loud on a non-GLB rather than emitting garbage
        raise ValueError(f"not a binary GLB (magic={magic:#x})")
    off = 12
    clen, _ctype = struct.unpack("<II", glb_bytes[off : off + 8])
    off += 8
    js = json.loads(glb_bytes[off : off + clen])
    off += clen
    bin_chunk = glb_bytes[off:]  # remaining chunks (incl. their own headers) copied verbatim
    for m in js.get("materials", []):
        if m.get("pbrMetallicRoughness", {}).get("baseColorTexture") is not None:
            m["alphaMode"] = "MASK"
            m["alphaCutoff"] = cutoff
            m["doubleSided"] = True
    new_js = json.dumps(js, separators=(",", ":")).encode()
    new_js += b" " * ((4 - len(new_js) % 4) % 4)  # 4-byte align JSON chunk
    header = struct.pack("<III", magic, ver, 12 + 8 + len(new_js) + len(bin_chunk))
    json_header = struct.pack("<II", len(new_js), 0x4E4F534A)  # 'JSON'
    return header + json_header + new_js + bin_chunk


class BlenderUnavailable(RuntimeError):
    """Raised when no usable Blender binary or leaf texture is available for the textured path."""


def to_textured_glb(
    obj_path: str,
    *,
    blender_bin: str = DEFAULT_BLENDER,
    leaf_tex: str = DEFAULT_LEAF_TEX,
    alpha_cutoff: float = 0.4,
    timeout_s: int = 300,
) -> bytes:
    """Run Blender headless to restore the alpha-cutout leaf texture, return MASK-patched GLB bytes."""
    if not Path(blender_bin).exists():
        raise BlenderUnavailable(f"Blender binary not found: {blender_bin} (set BLENDER_BIN)")
    if not Path(leaf_tex).exists():
        raise BlenderUnavailable(f"leaf texture not found: {leaf_tex} (set HELIOS_LEAF_TEXTURE)")
    with tempfile.TemporaryDirectory(prefix="helios_glb_") as td:
        script = Path(td) / "obj_to_glb.py"
        script.write_text(_BLENDER_SCRIPT)
        out_glb = Path(td) / "out.glb"
        proc = subprocess.run(
            [blender_bin, "-b", "-P", str(script), "--", obj_path, str(out_glb), leaf_tex],
            capture_output=True,
            timeout=timeout_s,
        )
        if not out_glb.exists():
            raise RuntimeError(
                f"Blender produced no GLB (exit {proc.returncode}): {proc.stderr.decode()[-500:]}"
            )
        return force_mask_alpha(out_glb.read_bytes(), cutoff=alpha_cutoff)
