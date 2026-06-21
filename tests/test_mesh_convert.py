import numpy as np
import pytest
import trimesh

from app.mesh_convert import MeshConvertError, to_glb


def test_to_glb_converts_a_mesh(tmp_path):
    obj = tmp_path / "box.obj"
    trimesh.creation.box().export(str(obj))
    glb = to_glb(str(obj))
    assert isinstance(glb, bytes) and glb[:4] == b"glTF"
    # round-trips back to a faced mesh
    loaded = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb", force="mesh")
    assert len(loaded.faces) > 0


def test_to_glb_rejects_point_cloud(tmp_path):
    ply = tmp_path / "cloud.ply"
    trimesh.PointCloud(np.random.rand(64, 3)).export(str(ply))
    with pytest.raises(MeshConvertError):
        to_glb(str(ply))


def test_to_glb_decimates_above_face_budget(tmp_path):
    # An icosphere subdivided to well above the budget; decimation must bring it under.
    dense = trimesh.creation.icosphere(subdivisions=5)  # ~20k faces
    obj = tmp_path / "dense.obj"
    dense.export(str(obj))
    assert len(dense.faces) > 5000
    glb = to_glb(str(obj), max_faces=2000)
    loaded = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb", force="mesh")
    assert 0 < len(loaded.faces) <= 2200  # at/under budget (decimator may slightly overshoot)


def test_to_glb_no_decimation_when_under_budget(tmp_path):
    box = trimesh.creation.box()  # 12 faces
    obj = tmp_path / "box.obj"
    box.export(str(obj))
    glb = to_glb(str(obj), max_faces=150_000)
    loaded = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb", force="mesh")
    assert len(loaded.faces) == 12  # untouched
