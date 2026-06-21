# tests/test_points_convert.py
import json
import struct

import numpy as np
import pytest
import trimesh

from app.points_convert import PointsConvertError, points_to_glb


def _primitive_modes(glb: bytes) -> list[int]:
    # GLB: 12-byte header, then a JSON chunk (8-byte chunk header + payload).
    clen = struct.unpack("<I", glb[12:16])[0]
    j = json.loads(glb[20 : 20 + clen].decode("utf-8"))
    return [p.get("mode") for m in j.get("meshes", []) for p in m.get("primitives", [])]


def test_points_to_glb_exports_points_primitive(tmp_path):
    ply = tmp_path / "cloud.ply"
    trimesh.PointCloud(np.random.RandomState(0).rand(500, 3)).export(str(ply))
    glb = points_to_glb(str(ply))
    assert isinstance(glb, bytes) and glb[:4] == b"glTF"
    assert _primitive_modes(glb) == [0]  # 0 == POINTS


def test_points_to_glb_downsamples_above_cap(tmp_path):
    ply = tmp_path / "big.ply"
    trimesh.PointCloud(np.random.RandomState(1).rand(5000, 3)).export(str(ply))
    glb = points_to_glb(str(ply), max_points=1000, seed=0)
    rt = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb")
    pts = rt.vertices if hasattr(rt, "vertices") else rt.geometry[next(iter(rt.geometry))].vertices
    assert len(pts) == 1000


def test_points_to_glb_preserves_colors(tmp_path):
    verts = np.random.RandomState(2).rand(300, 3)
    colors = np.tile(np.array([[10, 200, 30, 255]], dtype=np.uint8), (300, 1))
    ply = tmp_path / "colored.ply"
    trimesh.PointCloud(verts, colors=colors).export(str(ply))
    glb = points_to_glb(str(ply))
    rt = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb")
    geom = rt if hasattr(rt, "colors") else rt.geometry[next(iter(rt.geometry))]
    # The dominant green channel must survive the round-trip.
    assert geom.colors is not None and len(geom.colors) > 0
    mean = np.asarray(geom.colors)[:, :3].mean(axis=0)
    assert mean[1] > mean[0] and mean[1] > mean[2]


def test_points_to_glb_raises_on_empty(tmp_path):
    # trimesh's PLY exporter crashes on 0-vertex PointClouds (color dtype mismatch).
    # Write the empty PLY directly — the intent is to give points_to_glb a zero-vertex
    # file and assert PointsConvertError, not to test trimesh's export path.
    empty = tmp_path / "empty.ply"
    empty.write_bytes(
        b"ply\nformat binary_little_endian 1.0\n"
        b"element vertex 0\n"
        b"property float x\nproperty float y\nproperty float z\n"
        b"end_header\n"
    )
    with pytest.raises(PointsConvertError):
        points_to_glb(str(empty))
