"""A reduced opening frame for meshes whose bytes are TEXTURE, not geometry.

Measured against live R2 on 2026-08-07, sampling `uploads/` sources over 1 MB:

    group      median triangles   median texture share
    has LOD             500,000                 25.0%
    has none             31,250                 90.8%

The meshes without an LOD were never missed by the pipeline — they were correctly REFUSED.
`generate_lod` decimates geometry, and a file that is 90% texture bytes has almost no geometry
to remove, so it cannot reach `LOD_MIN_USEFUL_RATIO` and `worth_keeping` drops it. Running the
existing generator over more files cannot raise coverage; the constant was never the obstacle.

For those meshes the reducible dimension is the texture. 21 of 23 sampled images were still
1536x1536 (the stage-2 cap, PR #127), so a 768 cap is a ~4x cut in the bytes that actually
dominate the file.

This is the same contract the geometry LOD already signs: the reduced file is the OPENING frame
only, and the viewer swaps in the full mesh the moment anyone zooms or goes fullscreen. Texture
resolution is more perceptually salient than faceting on these organisms — colour and pattern
carry much of the judgement — which is why the cap is 768 rather than the 512 that would have
saved more.
"""

from __future__ import annotations

import io
import struct

import pytest

from app import mesh_compress as mc
from app import mesh_lod

PIL = pytest.importorskip("PIL.Image")
from PIL import Image  # noqa: E402

from tests.test_texture_downscale import build_glb  # noqa: E402


def _img(w: int, h: int, fmt: str = "PNG") -> bytes:
    """Structured noise. A flat fill would compress to almost nothing at every resolution, so
    the size ratio under test would be an artifact of the fixture rather than of the resize."""
    import random

    rnd = random.Random(7)
    im = Image.new("RGB", (w, h))
    im.putdata([(rnd.randrange(256), (x * 7) % 256, (x * 13) % 256) for x in range(w * h)])
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def _image_sizes(glb: bytes) -> list[tuple[int, int]]:
    g = mc.glb_json(glb)
    jlen = struct.unpack("<I", glb[12:16])[0]
    bin_off = 20 + jlen + 8
    out = []
    for im in g["images"]:
        v = g["bufferViews"][im["bufferView"]]
        off = bin_off + v["byteOffset"]
        out.append(Image.open(io.BytesIO(glb[off : off + v["byteLength"]])).size)
    return out


def test_a_texture_dominated_mesh_gets_an_lod_geometry_decimation_could_not_give_it():
    """THE case this exists for: 90% of the bytes are texture, so only the texture can shrink."""
    src = build_glb([_img(1536, 1536), _img(1536, 1536)])
    out = mesh_lod.texture_lod(src)

    assert out is not None, "a 1536px texture-dominated mesh must yield a texture LOD"
    assert mesh_lod.worth_keeping(len(src), len(out)), (
        f"LOD must earn its place: {len(src)} -> {len(out)} "
        f"is {len(src) / len(out):.2f}x, below {mesh_lod.LOD_MIN_USEFUL_RATIO}x"
    )
    assert all(max(s) <= mesh_lod.LOD_TEXTURE_MAX_DIM for s in _image_sizes(out))


def test_the_texture_lod_keeps_every_material_texture_and_attribute():
    """Integrity gate still applies. Losing a texture would look like a huge saving."""
    src = build_glb([_img(1536, 1536), _img(1536, 1536)])
    out = mesh_lod.texture_lod(src)
    assert out is not None
    mesh_lod.check_lod(src, out)  # raises if the model changed or the geometry collapsed
    assert len(_image_sizes(out)) == 2


def test_geometry_is_untouched_because_only_the_texture_was_reduced():
    """Distinguishes this path from the geometry LOD. Triangle count must be identical --
    otherwise a future reader could not tell which reduction produced a given file."""
    src = build_glb([_img(1536, 1536)])
    out = mesh_lod.texture_lod(src)
    assert out is not None
    assert mesh_lod.triangle_count(out) == mesh_lod.triangle_count(src)


def test_a_mesh_whose_textures_are_already_small_yields_nothing():
    """POSITIVE CONTROL for the gate. Without this, a function that returned None on every
    input would satisfy every 'is not None' assertion above by never running at all -- and an
    'already small' mesh must not be re-encoded, which costs fidelity and saves no bytes."""
    src = build_glb([_img(256, 256)])
    assert mesh_lod.texture_lod(src) is None


def test_a_reduction_that_does_not_earn_its_place_is_discarded():
    """The 1.5x rule is not waived for the texture path. A mesh dominated by GEOMETRY bytes
    barely shrinks when only its texture is capped, so it must be refused rather than shipped
    as a second file that saves nothing."""
    src = build_glb([_img(800, 800)], geom=b"\x01\x02\x03\x04" * 200_000)
    out = mesh_lod.texture_lod(src)
    if out is not None:
        assert mesh_lod.worth_keeping(len(src), len(out))


def test_texture_lod_never_upscales_a_small_texture_to_the_cap():
    """`target_size` returns None below the cap, so a 400px image stays 400px. An upscale would
    add bytes to the file whose whole purpose is to have fewer."""
    src = build_glb([_img(400, 400), _img(1536, 1536)])
    out = mesh_lod.texture_lod(src)
    assert out is not None
    sizes = sorted(max(s) for s in _image_sizes(out))
    assert sizes[0] == 400, f"small texture was altered: {sizes}"
    assert sizes[1] == mesh_lod.LOD_TEXTURE_MAX_DIM
