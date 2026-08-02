"""A LOD is a serving convenience. It must never become the thing a voter judges.

Draco (PR #124) could be gated on `structural_signature` alone because it *quantizes* positions
and keeps every triangle — 0.0017% of the bbox diagonal. Decimation is a different operation: it
DELETES triangles, and `structural_signature` cannot see that. A simplify pass that dropped 99% of
the geometry keeps the same mesh, material, texture and attribute counts, so the Draco gate would
wave it straight through.

The safety argument for LOD is therefore not "the difference is imperceptible" — at full zoom it
is not. It is that the full mesh is always one interaction away, and the viewer fetches it the
moment anyone looks closely. These tests pin the parts of that argument that live in Python: the
candidate rule, the triangle-floor guard, and the refusal to ship an LOD that changed the model or
failed to pay for its own second artifact.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

from app import mesh_lod


def _glb(meshes=1, materials=1, textures=0, images=0, tris=100, attrs=("POSITION",)):
    """A minimal but structurally valid GLB whose JSON chunk we can assert against."""
    accessors = [{"count": tris * 3}]
    prims = [{"attributes": {a: 0 for a in attrs}, "indices": 0, "mode": 4}]
    doc = {
        "asset": {"version": "2.0"},
        "accessors": accessors,
        "meshes": [{"primitives": prims} for _ in range(meshes)],
        "materials": [{} for _ in range(materials)],
        "textures": [{} for _ in range(textures)],
        "images": [{} for _ in range(images)],
    }
    raw = json.dumps(doc).encode()
    raw += b" " * ((4 - len(raw) % 4) % 4)
    return (
        b"glTF"
        + struct.pack("<II", 2, 12 + 8 + len(raw))
        + struct.pack("<II", len(raw), 0x4E4F534A)
        + raw
    )


# --------------------------------------------------------------------------- naming


def test_lod_path_is_derived_and_reversible():
    assert mesh_lod.lod_path("outputs/a.glb") == "outputs/a.lod.glb"
    assert mesh_lod.is_lod_path("outputs/a.lod.glb")
    assert not mesh_lod.is_lod_path("outputs/a.glb")


def test_lod_path_does_not_double_apply():
    """Re-running an export must not produce a.lod.lod.glb."""
    once = mesh_lod.lod_path("outputs/a.glb")
    assert mesh_lod.lod_path(once) == once


# --------------------------------------------------------------------------- candidacy


def test_small_meshes_are_not_candidates():
    """A second artifact costs bundle bytes and an import row. Below the floor it never pays."""
    assert not mesh_lod.is_lod_candidate("a.glb", mesh_lod.LOD_MIN_SOURCE_BYTES - 1)


def test_large_meshes_are_candidates():
    assert mesh_lod.is_lod_candidate("a.glb", mesh_lod.LOD_MIN_SOURCE_BYTES + 1)


def test_non_glb_is_never_a_candidate():
    """Point clouds and volumes go to entirely different viewers."""
    assert not mesh_lod.is_lod_candidate("a.ply", 50_000_000)
    assert not mesh_lod.is_lod_candidate("a.nii.gz", 50_000_000)


def test_an_lod_is_not_itself_a_candidate():
    """Guard against an export pass generating LODs of LODs."""
    assert not mesh_lod.is_lod_candidate("a.lod.glb", 50_000_000)


# --------------------------------------------------------------------------- triangles


def test_triangle_count_reads_indexed_primitives():
    assert mesh_lod.triangle_count(_glb(tris=1234)) == 1234


def test_triangle_count_sums_across_meshes():
    assert mesh_lod.triangle_count(_glb(meshes=3, tris=100)) == 300


# --------------------------------------------------------------------------- the gate


def test_collapsed_geometry_is_refused():
    """THE test this module exists for.

    `structural_signature` is identical before and after — same meshes, materials, textures,
    attributes — while 99.7% of the triangles are gone. Draco's gate would ship this.
    """
    before, after = _glb(tris=100_000), _glb(tris=300)
    assert mesh_lod.structural_signature(before) == mesh_lod.structural_signature(after)
    with pytest.raises(mesh_lod.LodCollapsed):
        mesh_lod.check_lod(before, after)


def test_a_reasonable_reduction_passes():
    """Positive control: the same gate must ADMIT a normal 4x decimation, or a broken gate
    would read as 'safely refused everything'."""
    before, after = _glb(tris=100_000), _glb(tris=25_000)
    mesh_lod.check_lod(before, after)


def test_dropping_a_texture_is_refused():
    """Decimation may remove triangles. It may not quietly remove the material a voter sees."""
    before = _glb(textures=2, images=2)
    after = _glb(textures=0, images=0, tris=25_000)
    with pytest.raises(mesh_lod.LodChangedTheModel):
        mesh_lod.check_lod(before, after)


def test_dropping_an_attribute_is_refused():
    before = _glb(attrs=("POSITION", "NORMAL", "TEXCOORD_0"))
    after = _glb(attrs=("POSITION",), tris=25_000)
    with pytest.raises(mesh_lod.LodChangedTheModel):
        mesh_lod.check_lod(before, after)


def test_an_lod_that_did_not_shrink_enough_is_not_worth_keeping():
    """Two artifacts for a 10% saving is a worse trade than one."""
    assert not mesh_lod.worth_keeping(10_000_000, 9_500_000)


def test_an_lod_that_shrank_well_is_worth_keeping():
    assert mesh_lod.worth_keeping(10_000_000, 2_000_000)


def test_an_lod_larger_than_its_source_is_refused():
    assert not mesh_lod.worth_keeping(1_000_000, 1_200_000)


def test_triangle_floor_scales_with_the_source():
    """A 200-triangle source cannot be held to a 1000-triangle floor."""
    before, after = _glb(tris=200), _glb(tris=120)
    mesh_lod.check_lod(before, after)


# --------------------------------------------------------------------------- command


def test_simplify_command_requests_a_ratio_and_an_error_bound():
    cmd = mesh_lod.simplify_command("node", "cli.js", "in.glb", "out.glb")
    assert cmd[:3] == ["node", "cli.js", "simplify"]
    assert "--ratio" in cmd and "--error" in cmd


def test_simplify_does_not_lock_borders():
    """Measured on a real 422-primitive corpus mesh: with borders locked, simplification removed
    400 of 1,625,148 triangles even with the error bound removed entirely. On a fragmented mesh
    nearly every edge IS a border."""
    cmd = mesh_lod.simplify_command("node", "cli.js", "in.glb", "out.glb")
    assert cmd[cmd.index("--lock-border") + 1] == "false"


def test_simplify_command_keeps_source_and_destination_distinct():
    cmd = mesh_lod.simplify_command("node", "cli.js", "in.glb", "out.glb")
    assert "in.glb" in cmd and "out.glb" in cmd


def test_every_stage_puts_the_destination_at_index_four():
    """`generate_lod` reads cmd[4] to confirm each stage actually wrote its output. If any
    command's argument order changed, that check would validate the wrong path."""
    for cmd in (
        mesh_lod.weld_command("node", "cli.js", "in.glb", "out.glb"),
        mesh_lod.simplify_command("node", "cli.js", "in.glb", "out.glb"),
        mesh_lod.draco_command("node", "cli.js", "in.glb", "out.glb"),
    ):
        assert cmd[3] == "in.glb"
        assert cmd[4] == "out.glb"


def test_the_pipeline_re_compresses_after_simplifying():
    """The stage that cost the most to discover: `simplify` DECODES incoming Draco and emits raw
    geometry — a 5.35 MB source came back at 123.68 MB, 23x larger. Without a draco stage every
    LOD exceeds its source and is silently rejected, so the export produces nothing while
    reporting success."""
    assert mesh_lod.draco_command("node", "cli.js", "a", "b")[2] == "draco"
    assert mesh_lod.weld_command("node", "cli.js", "a", "b")[2] == "weld"


# --------------------------------------------------------------------------- real execution

_REAL_MESH = Path("/home/mjarnold/bio3d-arena/public_bundle/v2/assets")
_NODE = Path("/home/mjarnold/miniconda3/envs/heirloom/bin/node")


def _toolchain():
    """The real toolchain, or None. Skipping is correct on a machine without it — the export
    refuses loudly there anyway (`ToolchainUnavailable`), so there is nothing to protect."""
    cli = os.environ.get("BIO3D_GLTF_TRANSFORM_CLI", "")
    node = os.environ.get("BIO3D_NODE_BIN", str(_NODE))
    if cli and Path(cli).exists() and Path(node).exists():
        return node, cli
    return None


@pytest.mark.skipif(_toolchain() is None, reason="gltf-transform toolchain not installed here")
def test_end_to_end_on_a_real_connected_corpus_mesh(tmp_path):
    """The synthetic fixtures above cannot catch a toolchain contract change; only this can.

    Two failures found exactly this way, neither visible to a mocked test:
      * `simplify` decodes Draco, so without the re-compress stage the LOD came out 23x LARGER;
      * `--lock-border true` pinned nearly every edge on a fragmented mesh and reduced 0.02%.
    """
    node, cli = _toolchain()
    candidates = sorted(
        (p for p in _REAL_MESH.rglob("*.glb") if p.stat().st_size > 3_000_000),
        key=lambda p: -p.stat().st_size,
    )
    if not candidates:
        pytest.skip("no corpus meshes present (this checkout has no data/)")

    reduced_any = False
    for src in candidates[:6]:
        dst = tmp_path / mesh_lod.lod_path(src.name)
        try:
            result = mesh_lod.generate_lod(src, dst, node=node, cli_entry=cli, timeout=600)
        except mesh_lod.LodCollapsed:
            continue  # the gate did its job; try the next mesh
        # Whatever happened, the model itself must have survived intact.
        assert mesh_lod.structural_signature(src.read_bytes()) == mesh_lod.structural_signature(
            (dst if dst.exists() else src).read_bytes()
        )
        if result.kept:
            reduced_any = True
            assert dst.exists(), "kept=True must leave the file on disk"
            assert result.lod_bytes < result.source_bytes
            assert result.lod_triangles < result.source_triangles
            break
        # Not kept: the file must NOT be left behind for the bundle to pick up.
        assert not dst.exists(), "a rejected LOD must be removed, never shipped"

    assert reduced_any, (
        "no corpus mesh produced a usable LOD — the pipeline is a no-op. "
        "This is the positive control: without it, a silently-broken toolchain reads as "
        "'nothing was worth compressing'."
    )
