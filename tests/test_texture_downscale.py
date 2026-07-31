"""Texture downscaling — stage 2 of the mesh-weight work.

Measured on the real corpus (2026-07-31): 452 embedded textures, 1.70 GB, and **422 of them are
>= 2048px on a side** while the arena renders meshes at ~300px on mobile and ~500px on desktop.
A 4096² texture carries 8-13x more resolution than the viewer can physically display, so the
bytes are simply thrown away by the GPU.

Two things this module exists to get right, both of which are easy to get wrong quietly:

* **The re-encode, not the resize, is where fidelity dies.** `gltf-transform resize` — the obvious
  tool — produced 28.5 dB PSNR at the viewer's render size, where >40 dB is imperceptible and
  <30 dB shows visible artifacts. Resizing with a controlled WebP encode reaches 42.2 dB at the
  SAME file size. Its default filter is already lanczos3 and the resolution drop itself is nearly
  free (56 dB without re-encoding), so the loss is purely a badly-tuned encoder.
* **Rewriting a GLB's binary chunk must be exact.** Every accessor addresses its data by
  bufferView offset. Shifting one offset by a byte does not fail loudly — it silently renders
  garbage geometry, which on a fidelity benchmark means voters judging noise.
"""

from __future__ import annotations

import io
import json
import struct

import pytest

from app import mesh_compress as mc
from app import texture_downscale as td

PIL = pytest.importorskip("PIL.Image")
from PIL import Image  # noqa: E402


def _img_bytes(w: int, h: int, fmt: str = "PNG") -> bytes:
    """A real image with structure — flat colour would compress to nothing and hide bugs."""
    import random

    rnd = random.Random(11)
    im = Image.new("RGB", (w, h))
    im.putdata([(rnd.randrange(256), (x * 7) % 256, (x * 13) % 256) for x in range(w * h)])
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def build_glb(images: list[bytes], geom: bytes = b"\x01\x02\x03\x04" * 8) -> bytes:
    """A GLB whose buffer holds one geometry view followed by one view per image."""
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(geom)}]
    blob = bytearray(geom)
    for data in images:
        pad = -len(blob) % 4
        blob.extend(b"\x00" * pad)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        blob.extend(data)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC4"}],
        "images": [{"bufferView": i + 1, "mimeType": "image/png"} for i in range(len(images))],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "materials": [{"name": "m"}],
        "textures": [{"source": i} for i in range(len(images))],
    }
    raw = json.dumps(gltf).encode()
    raw += b" " * (-len(raw) % 4)
    body = bytes(blob) + b"\x00" * (-len(blob) % 4)
    chunks = struct.pack("<II", len(raw), 0x4E4F534A) + raw
    chunks += struct.pack("<II", len(body), 0x004E4942) + body
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def _images_of(glb: bytes) -> list[Image.Image]:
    g = mc.glb_json(glb)
    jlen = struct.unpack("<I", glb[12:16])[0]
    bin_off = 20 + jlen + 8
    out = []
    for im in g["images"]:
        v = g["bufferViews"][im["bufferView"]]
        off = bin_off + v["byteOffset"]
        out.append(Image.open(io.BytesIO(glb[off : off + v["byteLength"]])).convert("RGB"))
    return out


# --------------------------------------------------------------------- policy


@pytest.mark.parametrize(
    "size,expected",
    [((4096, 4096), (1024, 1024)), ((2048, 1024), (1024, 512)), ((512, 512), None)],
)
def test_target_size_caps_the_long_edge_and_keeps_aspect(size, expected):
    """Aspect must be preserved — a squashed texture would slide across the model's UVs."""
    assert td.target_size(size, 1024) == expected


def test_a_texture_already_small_enough_is_left_alone():
    """Re-encoding an already-small texture would spend fidelity for no byte saving."""
    assert td.target_size((800, 600), 1024) is None


# --------------------------------------------------------------------- GLB rewrite


def test_downscale_shrinks_oversized_textures(tmp_path):
    glb = build_glb([_img_bytes(2048, 2048)])
    out, stats = td.downscale_glb(glb, max_dim=256, quality=92)
    assert stats["resized"] == 1
    assert [im.size for im in _images_of(out)] == [(256, 256)]
    assert len(out) < len(glb)


def test_geometry_is_bit_identical_after_a_texture_rewrite():
    """The load-bearing invariant. Accessors address data by bufferView offset, so a rewrite that
    shifts geometry by even one byte renders garbage — silently, with no error anywhere."""
    geom = bytes(range(256)) * 4
    glb = build_glb([_img_bytes(2048, 2048)], geom=geom)
    out, _ = td.downscale_glb(glb, max_dim=256, quality=92)

    g = mc.glb_json(out)
    jlen = struct.unpack("<I", out[12:16])[0]
    bin_off = 20 + jlen + 8
    v = g["bufferViews"][0]  # the geometry view
    assert out[bin_off + v["byteOffset"] : bin_off + v["byteOffset"] + v["byteLength"]] == geom


def test_the_rewritten_container_is_structurally_intact():
    glb = build_glb([_img_bytes(2048, 2048), _img_bytes(4096, 4096)])
    out, _ = td.downscale_glb(glb, max_dim=512, quality=92)
    before, after = mc.structural_signature(glb), mc.structural_signature(out)
    assert mc.structural_diff(before, after) == [], "the model changed shape, not just resolution"


def test_buffer_length_matches_the_bytes_actually_present():
    """A stale buffer.byteLength is the classic GLB corruption: some viewers trust the header and
    read past the end, others truncate. Both look like a broken model, neither like a bug here."""
    glb = build_glb([_img_bytes(2048, 2048)])
    out, _ = td.downscale_glb(glb, max_dim=256, quality=92)
    g = mc.glb_json(out)
    jlen = struct.unpack("<I", out[12:16])[0]
    bin_len = struct.unpack("<I", out[20 + jlen : 24 + jlen])[0]
    assert g["buffers"][0]["byteLength"] <= bin_len
    last = max(g["bufferViews"], key=lambda v: v["byteOffset"] + v["byteLength"])
    assert last["byteOffset"] + last["byteLength"] <= g["buffers"][0]["byteLength"]


def test_every_bufferview_stays_four_byte_aligned():
    """glTF requires it, and some loaders read typed arrays directly off the buffer."""
    glb = build_glb([_img_bytes(2048, 2048), _img_bytes(1024, 1024)])
    out, _ = td.downscale_glb(glb, max_dim=256, quality=92)
    for v in mc.glb_json(out)["bufferViews"]:
        assert v["byteOffset"] % 4 == 0, f"unaligned bufferView at {v['byteOffset']}"


def test_a_glb_with_no_textures_is_returned_untouched():
    glb = build_glb([])
    out, stats = td.downscale_glb(glb, max_dim=256, quality=92)
    assert stats["resized"] == 0
    assert out == glb, "a texture-free mesh was rewritten for no reason"


def test_small_textures_are_not_reencoded():
    """Positive control for the two rewrite tests: without this, a function that resized
    everything unconditionally would pass them both."""
    glb = build_glb([_img_bytes(128, 128)])
    out, stats = td.downscale_glb(glb, max_dim=1024, quality=92)
    assert stats["resized"] == 0
    assert out == glb
