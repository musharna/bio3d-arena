"""Unit tests for the pure-python L-Py GLB double-sided patch (no PlantGL/trimesh needed)."""

import json
import struct

import pytest

from scripts.lpy_glb import force_double_sided


def _make_glb(materials):
    js = {"asset": {"version": "2.0"}, "materials": materials}
    js_bytes = json.dumps(js, separators=(",", ":")).encode()
    js_bytes += b" " * ((4 - len(js_bytes) % 4) % 4)
    bin_payload = b"\x00\x00\x00\x00"
    bin_chunk = struct.pack("<II", len(bin_payload), 0x004E4942) + bin_payload
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js_bytes) + len(bin_chunk))
    return header + struct.pack("<II", len(js_bytes), 0x4E4F534A) + js_bytes + bin_chunk


def _materials(glb):
    clen, _ = struct.unpack("<II", glb[12:20])
    return json.loads(glb[20 : 20 + clen]).get("materials", [])


def test_force_double_sided_sets_flag_and_stays_valid():
    glb = _make_glb([{"name": "leaf"}, {"name": "stem"}])
    out = force_double_sided(glb)
    magic, _ver, length = struct.unpack("<III", out[:12])
    assert magic == 0x46546C67
    assert length == len(out)  # repack length correct
    assert all(m["doubleSided"] is True for m in _materials(out))


def test_force_double_sided_no_materials_is_noop():
    glb = _make_glb([])
    out = force_double_sided(glb)
    assert struct.unpack("<III", out[:12])[2] == len(out)  # still a valid GLB


def test_force_double_sided_rejects_non_glb():
    with pytest.raises(ValueError):
        force_double_sided(b"not a glb")
