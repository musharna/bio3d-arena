# app/texture_audit.py
"""Detect GLBs that render as a flat, single-colour blob in <model-viewer>.

Such a mesh carries no colour information — no texture image, no per-vertex COLOR_0, and no
material that actually tints it — so three.js shades it a uniform achromatic grey/white. It
loses perceptual votes for reasons unrelated to shape quality, which is why the Mode-A vote
pool excludes it (see app/sourcing.is_untextured_output). It stays on the metric boards.

What counts as colour: any texture image, any COLOR_0 attribute (even a single uniform colour —
a uniform-colour procedural plant HAS a colour and is NOT flagged), or any material whose
baseColorFactor carries hue. Only when EVERY material is achromatic — white, grey, black: the
RGB channels within _ACHROMATIC_TOLERANCE of each other — does the render carry no colour.

Why the material check exists (live audit, 2026-07-25)
-----------------------------------------------------
This function used to return False as soon as the GLB had ANY `materials` array. That is a
proxy for "no colour", not the property itself, and it is the wrong proxy: a Blender export
ALWAYS writes a materials array, so every LLM-authored colourless mesh passed the check. 22 of
515 votable outputs rendered as white/grey blobs in the arena; 16 were invisible here by
construction. The predicate now tests the documented property directly.

Fairness note: the commission brief never asks for colour and tells models "Keep material
setup minimal and defensive", so a colourless mesh is a COMPLIANT response to the brief, not a
model failure — the same reason geometry-only scans are pool-excluded rather than penalised.

Known trade-off: a genuinely white organism (Hericium erinaceus is white) modelled with a
deliberately white material is indistinguishable from one that never set a colour, so it is
flagged too. That is accepted: both render identically, so both carry the same perceptual
confound, and this gate is about the RENDER, not the author's intent.
"""

from __future__ import annotations

import json
import struct

# Max spread between R, G and B for a colour to count as achromatic (white/grey/black).
_ACHROMATIC_TOLERANCE = 0.06
# glTF default when a material omits baseColorFactor: opaque white.
_DEFAULT_BASE_COLOR = (1.0, 1.0, 1.0, 1.0)


def _glb_json(data: bytes) -> dict:
    """Parse the JSON chunk of a binary glTF (GLB) blob."""
    if data[:4] != b"glTF":
        raise ValueError("not a GLB")
    json_len = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20 : 20 + json_len].decode("utf-8"))


def _is_achromatic(material: dict) -> bool:
    """True if this material's base colour carries no hue (R≈G≈B)."""
    factor = material.get("pbrMetallicRoughness", {}).get("baseColorFactor")
    if not isinstance(factor, (list, tuple)) or len(factor) < 3:
        factor = _DEFAULT_BASE_COLOR  # absent or malformed -> glTF default white
    try:
        rgb = [float(c) for c in factor[:3]]
    except (TypeError, ValueError):
        return True  # unreadable channels tint nothing
    return (max(rgb) - min(rgb)) < _ACHROMATIC_TOLERANCE


def is_geometry_only_glb(data: bytes) -> bool:
    """True if the GLB renders as a flat achromatic blob (see module docstring).

    Malformed input returns False (fail open — never exclude an output just because we could
    not parse it).
    """
    try:
        j = _glb_json(data)
    except Exception:
        return False
    if j.get("images"):
        return False
    for mesh in j.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if "COLOR_0" in prim.get("attributes", {}):
                return False
    materials = j.get("materials") or []
    if not materials:
        return True  # no texture, no vertex colour, no material at all
    return all(_is_achromatic(m) for m in materials)
