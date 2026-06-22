"""Convert an L-Py / PlantGL tomato OBJ to a GLB for the spotlight.

PlantGL writes a Z-up OBJ+MTL with per-organ colours; model-viewer is Y-up and back-face culls.
This loads the OBJ preserving materials (NOT trimesh `force="mesh"`, which drops per-material
colour), rotates Z-up -> Y-up, exports GLB, and flags every material `doubleSided` so the flat
single-sided leaflet blades are visible from both sides. `force_double_sided` is pure-python GLB
JSON surgery (unit-tested); `obj_to_glb` needs trimesh.
"""

from __future__ import annotations

import json
import struct

_GLB_MAGIC = 0x46546C67  # 'glTF'
_JSON_CHUNK = 0x4E4F534A  # 'JSON'


def force_double_sided(glb_bytes: bytes) -> bytes:
    """Set doubleSided=True on every material; return a valid GLB (no-op if no materials)."""
    if len(glb_bytes) < 20:
        raise ValueError("not a binary GLB (too short)")
    magic, ver, _length = struct.unpack("<III", glb_bytes[:12])
    if magic != _GLB_MAGIC:
        raise ValueError(f"not a binary GLB (magic={magic:#x})")
    clen, _ctype = struct.unpack("<II", glb_bytes[12:20])
    js = json.loads(glb_bytes[20 : 20 + clen])
    bin_chunk = glb_bytes[20 + clen :]  # remaining chunks copied verbatim
    for m in js.get("materials", []):
        m["doubleSided"] = True
    new_js = json.dumps(js, separators=(",", ":")).encode()
    new_js += b" " * ((4 - len(new_js) % 4) % 4)  # 4-byte align
    header = struct.pack("<III", _GLB_MAGIC, ver, 12 + 8 + len(new_js) + len(bin_chunk))
    return header + struct.pack("<II", len(new_js), _JSON_CHUNK) + new_js + bin_chunk


def obj_to_glb(obj_path: str) -> bytes:
    """Load a PlantGL OBJ (materials preserved), Z-up->Y-up, return double-sided GLB bytes."""
    from math import pi

    import trimesh

    scene = trimesh.load(obj_path)  # Scene (no force="mesh" — keep per-material colour)
    scene.apply_transform(trimesh.transformations.rotation_matrix(-pi / 2, [1, 0, 0]))
    return force_double_sided(scene.export(file_type="glb"))
