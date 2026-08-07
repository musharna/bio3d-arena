"""Which reduction a mesh gets, and in which order.

Geometry first: it removes triangles a voter would otherwise download and leaves texture
resolution — the more perceptually salient dimension on these organisms — completely alone.
The texture tier is the FALLBACK, for the meshes geometry decimation provably cannot help.

Both refusal paths must fall through, not just the polite one. `generate_lod` signals "no useful
LOD" two different ways: a returned result with `kept=False`, and a raised `LodCollapsed` /
`LodChangedTheModel`. A fallback wired only into the first would silently skip every mesh whose
simplify pass collapsed — which is exactly the population most in need of the other tier.
"""

from __future__ import annotations

import io
import struct

import pytest

from app import mesh_lod

PIL = pytest.importorskip("PIL.Image")
from PIL import Image  # noqa: E402

from tests.test_texture_downscale import build_glb  # noqa: E402


def _img(w: int, h: int) -> bytes:
    import random

    rnd = random.Random(3)
    im = Image.new("RGB", (w, h))
    im.putdata([(rnd.randrange(256), (x * 5) % 256, (x * 11) % 256) for x in range(w * h)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _texture_heavy_glb() -> bytes:
    return build_glb([_img(1536, 1536)])


def _sizes(glb: bytes) -> list[int]:
    from app import mesh_compress as mc

    g = mc.glb_json(glb)
    jlen = struct.unpack("<I", glb[12:16])[0]
    off0 = 20 + jlen + 8
    out = []
    for im in g["images"]:
        v = g["bufferViews"][im["bufferView"]]
        off = off0 + v["byteOffset"]
        out.append(max(Image.open(io.BytesIO(glb[off : off + v["byteLength"]])).size))
    return out


def test_geometry_wins_when_it_earns_its_place(tmp_path, monkeypatch):
    """Positive control for the ordering. If this ever fails while the fallback tests pass,
    every mesh is silently getting the texture tier and the geometry path is dead code."""
    src = tmp_path / "m.glb"
    src.write_bytes(_texture_heavy_glb())
    dst = tmp_path / "m.lod.glb"

    def fake_generate(s, d, **kw):
        d.write_bytes(b"geometry-lod-bytes")
        return mesh_lod.LodResult(
            source_bytes=1000, lod_bytes=100, source_triangles=10, lod_triangles=3, kept=True
        )

    monkeypatch.setattr(mesh_lod, "generate_lod", fake_generate)
    assert mesh_lod.write_best_lod(src, dst, node="node", cli_entry="cli") == "geometry"
    assert dst.read_bytes() == b"geometry-lod-bytes"


def test_falls_back_to_texture_when_geometry_did_not_earn_its_place(tmp_path, monkeypatch):
    """kept=False is the common refusal: 94 of 184 uploads over 1 MB on live R2."""
    src = tmp_path / "m.glb"
    src.write_bytes(_texture_heavy_glb())
    dst = tmp_path / "m.lod.glb"

    def fake_generate(s, d, **kw):
        d.unlink(missing_ok=True)  # generate_lod removes a dst it will not vouch for
        return mesh_lod.LodResult(
            source_bytes=1000, lod_bytes=900, source_triangles=10, lod_triangles=9, kept=False
        )

    monkeypatch.setattr(mesh_lod, "generate_lod", fake_generate)
    assert mesh_lod.write_best_lod(src, dst, node="node", cli_entry="cli") == "texture"
    assert dst.is_file()
    assert _sizes(dst.read_bytes()) == [mesh_lod.LOD_TEXTURE_MAX_DIM]
    assert dst.stat().st_size < src.stat().st_size


def test_falls_back_to_texture_when_geometry_collapsed(tmp_path, monkeypatch):
    """The raising path. A collapsed simplify must not cost the mesh its other option."""
    src = tmp_path / "m.glb"
    src.write_bytes(_texture_heavy_glb())
    dst = tmp_path / "m.lod.glb"

    def fake_generate(s, d, **kw):
        raise mesh_lod.LodCollapsed("kept 1 of 10,000 triangles")

    monkeypatch.setattr(mesh_lod, "generate_lod", fake_generate)
    assert mesh_lod.write_best_lod(src, dst, node="node", cli_entry="cli") == "texture"
    assert dst.is_file()


def test_neither_tier_leaves_no_file_behind(tmp_path, monkeypatch):
    """A mesh with nothing to give must ship ONE file. A stale dst here would be served as a
    low-detail companion that is not actually smaller."""
    src = tmp_path / "m.glb"
    src.write_bytes(build_glb([_img(200, 200)]))  # already under the cap
    dst = tmp_path / "m.lod.glb"
    dst.write_bytes(b"stale-from-a-previous-run")

    def fake_generate(s, d, **kw):
        d.unlink(missing_ok=True)
        return mesh_lod.LodResult(
            source_bytes=1000, lod_bytes=999, source_triangles=10, lod_triangles=10, kept=False
        )

    monkeypatch.setattr(mesh_lod, "generate_lod", fake_generate)
    assert mesh_lod.write_best_lod(src, dst, node="node", cli_entry="cli") is None
    assert not dst.exists(), "a refused LOD must not leave a file the export would ship"
