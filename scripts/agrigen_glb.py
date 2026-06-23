"""Post-process an AgriGen tomato GLB: recolor the desaturated leaf organs to a saturated
tomato-foliage green. AgriGen writes per-organ PBR baseColorFactor (no textures), but its leaf
default is an olive/chartreuse (~0.3,0.4,0.2) that reads sickly under neutral lighting. This is a
pure-python GLB JSON patch (no Blender) so it is cheap and unit-testable.
"""

from __future__ import annotations

import json
import struct

_GLB_MAGIC = 0x46546C67  # 'glTF'
_JSON_CHUNK = 0x4E4F534A  # 'JSON'
# Saturated medium green typical of tomato foliage (linear-ish PBR base color).
LEAF_GREEN = (0.16, 0.42, 0.13, 1.0)


def _split_glb(glb_bytes: bytes):
    if len(glb_bytes) < 20:  # 12-byte GLB header + an 8-byte chunk header at minimum
        raise ValueError("not a binary GLB (too short)")
    magic, ver, _length = struct.unpack("<III", glb_bytes[:12])
    if magic != _GLB_MAGIC:
        raise ValueError(f"not a binary GLB (magic={magic:#x})")
    off = 12
    clen, _ctype = struct.unpack("<II", glb_bytes[off : off + 8])
    off += 8
    js = json.loads(glb_bytes[off : off + clen])
    off += clen
    return ver, js, glb_bytes[off:]  # bin_chunk copied verbatim (incl. its own header)


def _repack(ver: int, js: dict, bin_chunk: bytes) -> bytes:
    new_js = json.dumps(js, separators=(",", ":")).encode()
    new_js += b" " * ((4 - len(new_js) % 4) % 4)  # 4-byte align JSON chunk
    header = struct.pack("<III", _GLB_MAGIC, ver, 12 + 8 + len(new_js) + len(bin_chunk))
    return header + struct.pack("<II", len(new_js), _JSON_CHUNK) + new_js + bin_chunk


def recolor_leaves(glb_bytes: bytes, color=LEAF_GREEN) -> bytes:
    """Set baseColorFactor on every leaf/leaflet material to `color`; return a valid GLB.

    Targets materials whose name contains 'leaf' (AgriGen names organs main_leaf, branch_leaflet,
    etc.). Stems/branches/fruit are left as authored. No-op (still valid) if none match.
    """
    ver, js, bin_chunk = _split_glb(glb_bytes)
    rgba = [float(c) for c in color]
    for m in js.get("materials", []):
        if "leaf" in (m.get("name") or "").lower():
            m.setdefault("pbrMetallicRoughness", {})["baseColorFactor"] = rgba
    return _repack(ver, js, bin_chunk)


# AgriGen writes the plant growing along glTF +Z (a horizontal/depth axis), so <model-viewer>
# (glTF is +Y up) shows it lying on its side. A −90° rotation about X maps +Z → +Y (upright).
# Quaternion [x,y,z,w] for −90° about X = [sin(−45°), 0, 0, cos(−45°)].
_UPRIGHT_QUAT = [-0.7071067811865476, 0.0, 0.0, 0.7071067811865476]


def reorient_upright(glb_bytes: bytes, rotation=_UPRIGHT_QUAT) -> bytes:
    """Stand the plant up for +Y-up viewers by wrapping the default scene's root nodes under a new
    parent node carrying `rotation`. Composes with any existing node transforms (child TRS/matrix
    are preserved); returns a valid GLB. No-op (still valid) if the GLB has no scene nodes.
    """
    ver, js, bin_chunk = _split_glb(glb_bytes)
    nodes = js.setdefault("nodes", [])
    scenes = js.get("scenes") or []
    scene = scenes[js.get("scene", 0)] if scenes else None
    if not scene or not scene.get("nodes"):
        return _repack(ver, js, bin_chunk)
    wrapper = {"rotation": [float(c) for c in rotation], "children": list(scene["nodes"])}
    nodes.append(wrapper)
    scene["nodes"] = [len(nodes) - 1]
    return _repack(ver, js, bin_chunk)
