"""A GLB that renders as a flat achromatic blob must be detected, even when it HAS materials.

Live audit 2026-07-25: 22 of 515 votable outputs rendered white/grey in the arena, 16 of them
invisible to is_geometry_only_glb. Cause: the predicate returned False as soon as the GLB
carried any `materials` array — but a Blender export ALWAYS writes one, so every LLM-authored
colourless mesh passed. The check tested for a *proxy* (materials absent) instead of the
property its own docstring claims (renders as a flat single-colour blob).

Scope guard: the commission brief never asks for colour and says "Keep material setup minimal",
so a colourless mesh is a COMPLIANT response, not a model failure — it is excluded from the
Mode-A perceptual pool for the same reason geometry-only scans are ("loses votes for lack of
texture, not shape"), and stays on the metric boards.
"""

from __future__ import annotations

import json
import struct

from app.texture_audit import is_geometry_only_glb


def _glb(doc: dict) -> bytes:
    """Minimal binary-glTF container around a JSON chunk (no BIN chunk needed here)."""
    body = json.dumps(doc).encode("utf-8")
    body += b" " * (-len(body) % 4)  # chunks are 4-byte aligned
    chunk = struct.pack("<I", len(body)) + b"JSON" + body
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunk)) + chunk


def _mesh(*, color_0: bool = False) -> list:
    attrs = {"POSITION": 0}
    if color_0:
        attrs["COLOR_0"] = 1
    return [{"primitives": [{"attributes": attrs}]}]


def _mat(rgba):
    return {"pbrMetallicRoughness": {"baseColorFactor": list(rgba)}}


def test_white_material_with_no_texture_is_colourless():
    """The dominant real case: a Blender default-white material, no image, no vertex colour."""
    data = _glb({"meshes": _mesh(), "materials": [_mat([1.0, 1.0, 1.0, 1.0])]})
    assert is_geometry_only_glb(data) is True


def test_material_with_no_basecolor_defaults_to_white_and_is_colourless():
    """baseColorFactor is optional in glTF and defaults to [1,1,1,1] — absence is still white."""
    data = _glb({"meshes": _mesh(), "materials": [{"pbrMetallicRoughness": {}}]})
    assert is_geometry_only_glb(data) is True


def test_mid_grey_material_is_colourless():
    data = _glb({"meshes": _mesh(), "materials": [_mat([0.5, 0.5, 0.5, 1.0])]})
    assert is_geometry_only_glb(data) is True


def test_every_material_must_be_achromatic_to_count_as_colourless():
    """One genuinely coloured material means the render carries colour information."""
    data = _glb(
        {
            "meshes": _mesh(),
            "materials": [_mat([1.0, 1.0, 1.0, 1.0]), _mat([0.8, 0.1, 0.1, 1.0])],
        }
    )
    assert is_geometry_only_glb(data) is False


def test_coloured_material_is_not_colourless():
    """A red fly-agaric cap: has colour, judged on shape AND colour like any textured output."""
    data = _glb({"meshes": _mesh(), "materials": [_mat([0.85, 0.12, 0.09, 1.0])]})
    assert is_geometry_only_glb(data) is False


def test_texture_image_is_never_colourless():
    data = _glb(
        {
            "meshes": _mesh(),
            "images": [{"uri": "t.png"}],
            "materials": [_mat([1.0, 1.0, 1.0, 1.0])],
        }
    )
    assert is_geometry_only_glb(data) is False


def test_vertex_colours_are_never_colourless():
    """Preserved from the original contract: a uniform-COLOR_0 procedural plant HAS a colour."""
    data = _glb({"meshes": _mesh(color_0=True), "materials": [_mat([1.0, 1.0, 1.0, 1.0])]})
    assert is_geometry_only_glb(data) is False


def test_no_materials_at_all_is_still_colourless():
    """The original case must keep working: no images, no materials, no COLOR_0."""
    assert is_geometry_only_glb(_glb({"meshes": _mesh()})) is True


def test_malformed_input_fails_open():
    """Never exclude an output just because we could not parse it."""
    assert is_geometry_only_glb(b"not a glb at all") is False
