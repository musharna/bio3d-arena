"""Tests for geometry-only GLB detection (flat-grey-blob outputs)."""

from __future__ import annotations

import json
import struct

from app.texture_audit import is_geometry_only_glb


def _mk_glb(doc: dict) -> bytes:
    """Wrap a glTF JSON doc into minimal GLB bytes (JSON chunk only — enough for the detector)."""
    payload = json.dumps(doc).encode("utf-8")
    while len(payload) % 4:
        payload += b" "
    header = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(payload))
    chunk = struct.pack("<II", len(payload), 0x4E4F534A) + payload  # 0x4E4F534A == "JSON"
    return header + chunk


def test_position_only_is_geometry_only():
    glb = _mk_glb({"meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}]})
    assert is_geometry_only_glb(glb) is True


def test_color0_is_not_geometry_only():
    glb = _mk_glb({"meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}}]}]})
    assert is_geometry_only_glb(glb) is False


def test_colourless_material_is_still_geometry_only():
    """CONTRACT CHANGE (live audit 2026-07-25): the mere PRESENCE of a materials array no
    longer clears a GLB. A material with no baseColorFactor defaults to white and tints
    nothing, so this renders as the same flat blob as no material at all — and because a
    Blender export always writes a materials array, the old assertion made every
    LLM-authored colourless mesh invisible to the detector. See test_texture_audit_colourless.py."""
    glb = _mk_glb(
        {
            "materials": [{"name": "m"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        }
    )
    assert is_geometry_only_glb(glb) is True


def test_coloured_material_is_not_geometry_only():
    """What the replaced test was reaching for: a material that actually tints the mesh."""
    glb = _mk_glb(
        {
            "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.85, 0.12, 0.09, 1.0]}}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        }
    )
    assert is_geometry_only_glb(glb) is False


def test_texture_image_is_not_geometry_only():
    glb = _mk_glb(
        {
            "images": [{"uri": "t.png"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        }
    )
    assert is_geometry_only_glb(glb) is False


def test_malformed_fails_open_false():
    assert is_geometry_only_glb(b"not a glb") is False
    assert is_geometry_only_glb(b"") is False
