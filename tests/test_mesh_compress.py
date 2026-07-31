"""Draco compression of served meshes.

Corpus GLBs ship uncompressed: 3.64 GB of the 5.35 GB votable roster is raw accessor data, and
nothing carries Draco, meshopt or KTX2. Measured consequence (Playwright + CDP, Chrome "Fast 4G",
4 Mbit/s): a PAIRWISE ballot — what the public site serves today — takes 83 seconds before a voter
can compare anything. A Draco pilot on two real corpus meshes returned 13.7x and 5.1x at a mean
geometric deviation of 0.0017% of the bounding-box diagonal.

Two rules this module exists to enforce, both of which are easy to violate silently:

* Compression is a SERVING concern. It happens at export, so the internal corpus stays
  byte-identical — reproducibility, the dataset release, and the votes already cast against those
  exact meshes all depend on the originals not moving.
* A fidelity benchmark must not quietly degrade its own stimuli. Draco reorders vertices and
  rewrites accessors; if it also dropped a UV set or a material, every downstream comparison would
  be judging something other than what was scored. The structural signature is checked on every
  file, not sampled.
"""

from __future__ import annotations

import json
import pathlib
import struct
import subprocess

import pytest

from app import mesh_compress


def build_glb(gltf: dict, bin_chunk: bytes = b"") -> bytes:
    """A minimal but real GLB container, so these tests need no fixture files and no toolchain."""
    raw = json.dumps(gltf).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    out = b"JSON"  # placeholder, replaced below
    chunks = struct.pack("<II", len(raw), 0x4E4F534A) + raw
    if bin_chunk:
        padded = bin_chunk + b"\x00" * (-len(bin_chunk) % 4)
        chunks += struct.pack("<II", len(padded), 0x004E4942) + padded
    out = b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks
    return out


_MINIMAL = {
    "asset": {"version": "2.0"},
    "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}}]}],
    "materials": [{"name": "m"}],
    "textures": [{"source": 0}],
    "images": [{"bufferView": 0}],
}


# --------------------------------------------------------------------- container parsing


def test_reads_the_json_chunk_of_a_glb():
    data = build_glb(_MINIMAL, b"\x01\x02\x03\x04")
    assert mesh_compress.glb_json(data)["asset"]["version"] == "2.0"


def test_rejects_a_file_that_is_not_a_glb():
    with pytest.raises(mesh_compress.NotAGlb):
        mesh_compress.glb_json(b"this is a plain PNG, not a mesh")


# --------------------------------------------------------------------- structural signature


def test_signature_captures_what_a_voter_would_notice_going_missing():
    sig = mesh_compress.structural_signature(build_glb(_MINIMAL))
    assert sig["materials"] == 1
    assert sig["textures"] == 1
    assert sig["images"] == 1
    assert sig["attributes"] == ("NORMAL", "POSITION", "TEXCOORD_0")


def test_identical_files_have_no_structural_difference():
    a = mesh_compress.structural_signature(build_glb(_MINIMAL))
    assert mesh_compress.structural_diff(a, dict(a)) == []


def test_a_dropped_uv_set_is_reported():
    """The failure mode that matters: Draco rewrites every accessor, so a tool bug that lost
    TEXCOORD_0 would strip the texture mapping while leaving a file that still loads and still
    looks like a mesh — and every subsequent vote would be cast on different stimuli."""
    before = mesh_compress.structural_signature(build_glb(_MINIMAL))
    stripped = json.loads(json.dumps(_MINIMAL))
    del stripped["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
    after = mesh_compress.structural_signature(build_glb(stripped))
    diff = mesh_compress.structural_diff(before, after)
    assert diff, "a dropped UV set was not reported"
    assert any("attributes" in d for d in diff)


def test_a_dropped_material_is_reported():
    before = mesh_compress.structural_signature(build_glb(_MINIMAL))
    stripped = json.loads(json.dumps(_MINIMAL))
    stripped["materials"] = []
    after = mesh_compress.structural_signature(build_glb(stripped))
    assert any("materials" in d for d in mesh_compress.structural_diff(before, after))


# --------------------------------------------------------------------- toolchain guard


@pytest.mark.parametrize(
    "raw,expected",
    [("v24.18.0", 24), ("v20.0.0", 20), ("v18.19.1", 18), ("garbage", None), ("", None)],
)
def test_parses_node_version(raw, expected):
    assert mesh_compress.parse_node_major(raw) == expected


def test_toolchain_below_the_floor_fails_loudly_and_says_why(monkeypatch):
    """System Node is 18.19 here and the CLI dies on import-attribute syntax with a raw
    SyntaxError several frames from the cause. Refuse up front instead."""
    monkeypatch.setattr(mesh_compress, "detect_node_major", lambda binary: 18)
    with pytest.raises(mesh_compress.ToolchainUnavailable) as e:
        mesh_compress.require_node("node")
    msg = str(e.value)
    assert "20" in msg and "18" in msg, msg


def test_toolchain_absent_fails_loudly(monkeypatch):
    monkeypatch.setattr(mesh_compress, "detect_node_major", lambda binary: None)
    with pytest.raises(mesh_compress.ToolchainUnavailable):
        mesh_compress.require_node("definitely-not-node")


def test_accepts_node_at_or_above_the_floor(monkeypatch):
    """Positive control for the two refusals above — without it a broken detector would read
    as 'correctly refusing' in every case."""
    monkeypatch.setattr(mesh_compress, "detect_node_major", lambda binary: 20)
    assert mesh_compress.require_node("node") == 20


# --------------------------------------------------------------------- command construction


def test_draco_command_targets_the_requested_files(tmp_path):
    src, dst = tmp_path / "a.glb", tmp_path / "b.glb"
    cmd = mesh_compress.draco_command("nodebin", "/cli/entry.js", src, dst)
    assert cmd[0] == "nodebin"
    assert cmd[1] == "/cli/entry.js"
    assert "draco" in cmd
    assert str(src) in cmd and str(dst) in cmd
    assert cmd.index(str(src)) < cmd.index(str(dst)), "src/dst order is load-bearing"


# --------------------------------------------------------------------- policy


def test_compression_that_grows_a_file_is_refused(tmp_path):
    """Draco on an already-compressed or pathologically small mesh can produce a LARGER file.
    Serving that would be strictly worse than doing nothing, so the caller keeps the original."""
    assert mesh_compress.worth_keeping(original_size=100, compressed_size=40) is True
    assert mesh_compress.worth_keeping(original_size=100, compressed_size=99) is False
    assert mesh_compress.worth_keeping(original_size=100, compressed_size=120) is False


# --------------------------------------------------------------------- guard paths
# These drive compress_glb() with a stubbed CLI so the REFUSAL branches run everywhere, CI
# included, where there is no Node. A guard that never executes in CI is a guard nobody can trust.


def _stub_tool(monkeypatch, produce: bytes):
    """Stand in for the CLI: writes `produce` to the destination and exits 0."""

    def fake_run(cmd, **kw):
        pathlib.Path(cmd[-1]).write_bytes(produce)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mesh_compress.subprocess, "run", fake_run)


def test_refuses_output_that_changed_the_model(tmp_path, monkeypatch):
    """The guard must FIRE, not merely exist. Feed it a 'compressed' file whose UV set vanished
    and require both the refusal and the removal of the unusable artifact."""
    src = tmp_path / "in.glb"
    src.write_bytes(build_glb(_MINIMAL, b"\x00" * 4096))
    stripped = json.loads(json.dumps(_MINIMAL))
    del stripped["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
    _stub_tool(monkeypatch, build_glb(stripped))
    dst = tmp_path / "out.glb"

    with pytest.raises(mesh_compress.CompressionChangedTheModel) as e:
        mesh_compress.compress_glb(src, dst, node="node", cli_entry="cli")
    assert "attributes" in str(e.value)
    assert not dst.exists(), "a model-altering artifact was left where it could ship"


def test_keeps_the_original_when_compression_does_not_help(tmp_path, monkeypatch):
    src = tmp_path / "in.glb"
    src.write_bytes(build_glb(_MINIMAL, b"\x00" * 1024))
    _stub_tool(monkeypatch, build_glb(_MINIMAL, b"\x00" * 8192))  # bigger than the source
    res = mesh_compress.compress_glb(src, tmp_path / "out.glb", node="node", cli_entry="cli")
    assert res.kept is False
    assert not (tmp_path / "out.glb").exists()


def test_keeps_the_compressed_file_when_it_helps(tmp_path, monkeypatch):
    """Positive control for both refusals above — without it, an always-refuse bug would look
    identical to correct behaviour."""
    src = tmp_path / "in.glb"
    src.write_bytes(build_glb(_MINIMAL, b"\x00" * 65536))
    _stub_tool(monkeypatch, build_glb(_MINIMAL, b"\x00" * 512))
    dst = tmp_path / "out.glb"
    res = mesh_compress.compress_glb(src, dst, node="node", cli_entry="cli")
    assert res.kept is True and dst.exists() and res.ratio > 1.05


def test_a_failing_tool_raises_rather_than_shipping_nothing(tmp_path, monkeypatch):
    src = tmp_path / "in.glb"
    src.write_bytes(build_glb(_MINIMAL, b"\x00" * 4096))
    monkeypatch.setattr(
        mesh_compress.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "draco: boom"),
    )
    with pytest.raises(RuntimeError, match="boom"):
        mesh_compress.compress_glb(src, tmp_path / "out.glb", node="node", cli_entry="cli")


# --------------------------------------------------------------------- real execution
# The stubs prove the branches; only this proves the actual CLI invocation is correct.
# Skipped where the toolchain is absent — deliberately visible, never silent.


@pytest.mark.skipif(
    not mesh_compress.toolchain_ready(),
    reason="needs Node >= 20 and BIO3D_GLTF_TRANSFORM_CLI pointing at the gltf-transform CLI",
)
def test_real_toolchain_compresses_a_real_mesh(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    mesh = trimesh.creation.icosphere(subdivisions=5)  # dense enough for Draco to bite
    src = tmp_path / "sphere.glb"
    src.write_bytes(trimesh.Scene(mesh).export(file_type="glb"))
    dst = tmp_path / "sphere_draco.glb"

    res = mesh_compress.compress_glb(
        src, dst, node=mesh_compress.node_binary(), cli_entry=mesh_compress.cli_entry()
    )
    assert res.kept is True
    assert res.ratio > 1.5, f"real Draco run only achieved {res.ratio:.2f}x"
    assert (
        mesh_compress.structural_diff(
            mesh_compress.structural_signature(src.read_bytes()),
            mesh_compress.structural_signature(dst.read_bytes()),
        )
        == []
    )


# --------------------------------------------------------------------- export wiring


class _FakeStorage:
    def __init__(self, blobs):
        self.blobs = blobs

    def read(self, rel):
        return self.blobs[rel]


def _stage(tmp_path, monkeypatch, *, compress, blobs, fake_result=None):
    from scripts import export_public

    if fake_result is not None:

        def fake_compress(src, dst, **kw):
            dst.write_bytes(b"x" * fake_result)
            return mesh_compress.CompressionResult(src.stat().st_size, fake_result, kept=True)

        monkeypatch.setattr(export_public.mesh_compress, "compress_glb", fake_compress)
    monkeypatch.setattr(export_public.mesh_compress, "require_node", lambda b: 20)
    monkeypatch.setattr(export_public.mesh_compress, "cli_entry", lambda: __file__)
    rows = [{"asset_path": k} for k in blobs]
    return export_public._stage_assets(tmp_path, _FakeStorage(blobs), rows, compress=compress)


def _real_glb(pad: int = 40000) -> bytes:
    """A structurally valid GLB. The export parses containers now (texture downscaling), so a
    `b"g" * 40000` stand-in no longer stands in for anything — it is exactly the corrupt-file
    case, which the export is supposed to reject."""
    return build_glb(_MINIMAL, b"\x00" * pad)


def test_export_compresses_glbs_and_reports_the_ratio(tmp_path, monkeypatch):
    blobs = {"uploads/a.glb": _real_glb(), "uploads/b.glb": _real_glb()}
    stats = _stage(tmp_path, monkeypatch, compress=True, blobs=blobs, fake_result=4000)
    assert stats["compressed"] == 2
    # ~10x, not exactly: a real GLB carries a JSON chunk and 4-byte padding on top of the
    # payload, so pinning an exact float would be pinning the fixture, not the behaviour.
    assert 9.5 < stats["ratio"] < 10.5
    assert stats["ratio"] == pytest.approx(stats["bytes_before"] / stats["bytes_after"], rel=1e-3)
    for rel in blobs:
        assert (tmp_path / "assets" / rel).stat().st_size == 4000, "bundle kept the original"


def test_export_leaves_non_glb_assets_alone(tmp_path, monkeypatch):
    """A .ply point cloud or a reference JPEG must reach the bundle untouched — Draco would
    either fail or silently produce something the viewer for that format cannot read."""
    blobs = {"uploads/cloud.ply": b"p" * 5000, "uploads/m.glb": _real_glb()}
    stats = _stage(tmp_path, monkeypatch, compress=True, blobs=blobs, fake_result=4000)
    assert stats["compressed"] == 1
    assert stats["skipped_not_glb"] == 1
    assert (tmp_path / "assets" / "uploads/cloud.ply").read_bytes() == b"p" * 5000


def test_no_compress_ships_the_originals_and_needs_no_toolchain(tmp_path, monkeypatch):
    """The escape hatch must not require Node at all — that is its whole purpose. `require_node`
    is stubbed to explode so any call to it fails this test."""
    from scripts import export_public

    monkeypatch.setattr(
        export_public.mesh_compress,
        "require_node",
        lambda b: (_ for _ in ()).throw(AssertionError("toolchain consulted under --no-compress")),
    )
    blobs = {"uploads/a.glb": _real_glb()}
    rows = [{"asset_path": k} for k in blobs]
    stats = export_public._stage_assets(tmp_path, _FakeStorage(blobs), rows, compress=False)
    assert stats["enabled"] is False
    assert stats["compressed"] == 0
    assert (tmp_path / "assets" / "uploads/a.glb").read_bytes() == blobs["uploads/a.glb"]


def test_a_corrupt_glb_fails_the_export_and_names_the_asset(tmp_path, monkeypatch):
    """A file carrying a .glb extension that is not a GLB is a corpus problem, not something to
    route around — it would reach voters as a mesh that never loads. It must fail, and the error
    must name the asset: an anonymous ValueError partway through a 939-file export tells the
    operator nothing about which file to go and look at."""
    from scripts import export_public

    monkeypatch.setattr(export_public.mesh_compress, "require_node", lambda b: 20)
    monkeypatch.setattr(export_public.mesh_compress, "cli_entry", lambda: __file__)
    blobs = {"uploads/broken.glb": b"this is a JPEG that got the wrong extension"}
    rows = [{"asset_path": k} for k in blobs]
    with pytest.raises(ValueError, match="uploads/broken.glb"):
        export_public._stage_assets(tmp_path, _FakeStorage(blobs), rows, compress=True)


def test_only_glb_is_a_compression_candidate():
    assert mesh_compress.is_candidate("uploads/x.glb") is True
    assert mesh_compress.is_candidate("uploads/x.GLB") is True
    # Point clouds / volumes / molecular formats are served by other viewers entirely.
    assert mesh_compress.is_candidate("uploads/x.ply") is False
    assert mesh_compress.is_candidate("uploads/x.pdb") is False
    assert mesh_compress.is_candidate("reference/gallery/rosa/1.jpg") is False
