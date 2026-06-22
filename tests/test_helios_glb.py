"""Unit tests for the pure-python GLB MASK patch (no Blender needed)."""

import json
import struct

import trimesh

from scripts.helios_glb import force_mask_alpha


def _glb_materials(glb_bytes):
    off = 12
    clen, _ = struct.unpack("<II", glb_bytes[off : off + 8])
    off += 8
    return json.loads(glb_bytes[off : off + clen])


def _box_glb():
    return trimesh.creation.box().export(file_type="glb")


def test_force_mask_alpha_roundtrips_and_is_valid_glb():
    """An untextured mesh has no base-color texture -> patch is a structural no-op but stays valid."""
    out = force_mask_alpha(_box_glb(), cutoff=0.4)
    magic, _ver, length = struct.unpack("<III", out[:12])
    assert magic == 0x46546C67  # 'glTF'
    assert length == len(out)  # header length matches actual byte length (chunk re-packing correct)
    js = _glb_materials(out)
    # no textured material -> nothing forced to MASK
    assert all(m.get("alphaMode") != "MASK" for m in js.get("materials", []))


def test_force_mask_alpha_sets_mask_on_textured_material():
    """A material with a base-color texture is forced to MASK + doubleSided with the given cutoff."""
    # hand-build a minimal valid GLB JSON carrying one textured + one colored material
    base = {
        "asset": {"version": "2.0"},
        "materials": [
            {"name": "leaf", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            {"name": "fruit", "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.1, 0.1, 1]}},
        ],
    }
    js_bytes = json.dumps(base, separators=(",", ":")).encode()
    js_bytes += b" " * ((4 - len(js_bytes) % 4) % 4)
    bin_payload = b"\x00\x00\x00\x00"
    bin_chunk = struct.pack("<II", len(bin_payload), 0x004E4942) + bin_payload
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js_bytes) + len(bin_chunk))
    glb = header + struct.pack("<II", len(js_bytes), 0x4E4F534A) + js_bytes + bin_chunk

    out = force_mask_alpha(glb, cutoff=0.35)
    mats = {m["name"]: m for m in _glb_materials(out)["materials"]}
    assert mats["leaf"]["alphaMode"] == "MASK"
    assert mats["leaf"]["alphaCutoff"] == 0.35
    assert mats["leaf"]["doubleSided"] is True
    # the colored (untextured) material is untouched
    assert "alphaMode" not in mats["fruit"]


def test_force_mask_alpha_rejects_non_glb():
    import pytest

    with pytest.raises(ValueError):
        force_mask_alpha(b"not a glb at all", cutoff=0.4)
