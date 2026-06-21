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
