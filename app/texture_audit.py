# app/texture_audit.py
"""Detect GLBs that render as a flat, single-colour blob in <model-viewer>.

A "geometry-only" GLB carries POSITION but no texture image, no material, and no per-vertex
COLOR_0 — so three.js shades it with the default grey, producing an uninformative blob that
loses perceptual votes for reasons unrelated to shape quality. This is narrower than "untextured":
a uniform-colour procedural plant (single COLOR_0) still has a colour and full geometry, so it is
NOT flagged. Used to keep such geometry-only outputs out of the Mode-A perceptual pool.
"""

from __future__ import annotations

import json
import struct


def _glb_json(data: bytes) -> dict:
    """Parse the JSON chunk of a binary glTF (GLB) blob."""
    if data[:4] != b"glTF":
        raise ValueError("not a GLB")
    json_len = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20 : 20 + json_len].decode("utf-8"))


def is_geometry_only_glb(data: bytes) -> bool:
    """True if the GLB has no texture image, no material, and no COLOR_0 attribute.

    Such a mesh renders as a flat default-grey blob. A GLB with a texture, a material, or any
    per-vertex colour (even uniform) returns False. Malformed input returns False (fail open —
    never exclude an output just because we could not parse it).
    """
    try:
        j = _glb_json(data)
    except Exception:
        return False
    if j.get("images") or j.get("materials"):
        return False
    for mesh in j.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if "COLOR_0" in prim.get("attributes", {}):
                return False
    return True
