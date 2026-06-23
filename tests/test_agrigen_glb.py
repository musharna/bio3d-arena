"""Unit tests for the pure-python AgriGen leaf recolor (no AgriGen/Blender needed)."""

import json
import struct

import pytest

from scripts.agrigen_glb import LEAF_GREEN, recolor_leaves, reorient_upright


def _make_glb(materials, *, nodes=None, scenes=None):
    js = {"asset": {"version": "2.0"}, "materials": materials}
    if nodes is not None:
        js["nodes"] = nodes
    if scenes is not None:
        js["scenes"] = scenes
        js["scene"] = 0
    js_bytes = json.dumps(js, separators=(",", ":")).encode()
    js_bytes += b" " * ((4 - len(js_bytes) % 4) % 4)
    bin_payload = b"\x00\x00\x00\x00"
    bin_chunk = struct.pack("<II", len(bin_payload), 0x004E4942) + bin_payload
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js_bytes) + len(bin_chunk))
    return header + struct.pack("<II", len(js_bytes), 0x4E4F534A) + js_bytes + bin_chunk


def _json(glb):
    off = 12
    clen, _ = struct.unpack("<II", glb[off : off + 8])
    off += 8
    return json.loads(glb[off : off + clen])


def _materials(glb):
    off = 12
    clen, _ = struct.unpack("<II", glb[off : off + 8])
    off += 8
    return {m["name"]: m for m in json.loads(glb[off : off + clen])["materials"]}


def test_recolor_targets_leaves_only_and_stays_valid():
    glb = _make_glb(
        [
            {"name": "main_stem", "pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.5, 0.15, 1]}},
            {
                "name": "branch_leaflet",
                "pbrMetallicRoughness": {"baseColorFactor": [0.3, 0.4, 0.2, 1]},
            },
            {"name": "main_leaf", "pbrMetallicRoughness": {"baseColorFactor": [0.3, 0.4, 0.2, 1]}},
        ]
    )
    out = recolor_leaves(glb)
    # valid GLB: header length matches actual bytes (chunk repack correct)
    magic, _ver, length = struct.unpack("<III", out[:12])
    assert magic == 0x46546C67
    assert length == len(out)
    mats = _materials(out)
    assert mats["branch_leaflet"]["pbrMetallicRoughness"]["baseColorFactor"] == list(LEAF_GREEN)
    assert mats["main_leaf"]["pbrMetallicRoughness"]["baseColorFactor"] == list(LEAF_GREEN)
    # stem (no 'leaf' in name) is untouched
    assert mats["main_stem"]["pbrMetallicRoughness"]["baseColorFactor"] == [0.2, 0.5, 0.15, 1]


def test_recolor_custom_color_and_no_leaf_is_noop():
    glb = _make_glb(
        [{"name": "stem", "pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.5, 0.15, 1]}}]
    )
    out = recolor_leaves(glb, color=(0.1, 0.3, 0.1, 1.0))
    # no leaf material -> still a valid, structurally-intact GLB, stem unchanged
    assert _materials(out)["stem"]["pbrMetallicRoughness"]["baseColorFactor"] == [0.2, 0.5, 0.15, 1]


def test_recolor_rejects_non_glb():
    with pytest.raises(ValueError):
        recolor_leaves(b"not a glb")


def test_reorient_wraps_roots_under_rotated_parent():
    # two root nodes in the default scene; reorient should wrap them under one rotated parent
    glb = _make_glb(
        [{"name": "stem", "pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.5, 0.15, 1]}}],
        nodes=[{"name": "stem_node"}, {"name": "leaf_node"}],
        scenes=[{"nodes": [0, 1]}],
    )
    out = reorient_upright(glb)
    magic, _ver, length = struct.unpack("<III", out[:12])
    assert magic == 0x46546C67
    assert length == len(out)  # valid repack
    js = _json(out)
    # scene now points at a single new wrapper node...
    assert len(js["scenes"][0]["nodes"]) == 1
    wrapper_idx = js["scenes"][0]["nodes"][0]
    wrapper = js["nodes"][wrapper_idx]
    # ...which carries the upright rotation and the original roots as children
    assert wrapper["children"] == [0, 1]
    assert wrapper["rotation"][0] == pytest.approx(-0.70710678, abs=1e-6)
    assert wrapper["rotation"][3] == pytest.approx(0.70710678, abs=1e-6)


def test_reorient_no_scene_nodes_is_noop_but_valid():
    glb = _make_glb(
        [{"name": "stem", "pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.5, 0.15, 1]}}]
    )
    out = reorient_upright(glb)
    magic, _ver, length = struct.unpack("<III", out[:12])
    assert magic == 0x46546C67
    assert length == len(out)
