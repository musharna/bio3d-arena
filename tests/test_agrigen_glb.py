"""Unit tests for the pure-python AgriGen leaf recolor (no AgriGen/Blender needed)."""

import json
import struct

import pytest

from scripts.agrigen_glb import LEAF_GREEN, recolor_leaves


def _make_glb(materials):
    js = {"asset": {"version": "2.0"}, "materials": materials}
    js_bytes = json.dumps(js, separators=(",", ":")).encode()
    js_bytes += b" " * ((4 - len(js_bytes) % 4) % 4)
    bin_payload = b"\x00\x00\x00\x00"
    bin_chunk = struct.pack("<II", len(bin_payload), 0x004E4942) + bin_payload
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js_bytes) + len(bin_chunk))
    return header + struct.pack("<II", len(js_bytes), 0x4E4F534A) + js_bytes + bin_chunk


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
