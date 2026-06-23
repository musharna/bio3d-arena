import io

import numpy as np
import pytest
import tifffile
import trimesh

from app.volume_convert import VolumeConvertError, volume_to_glb


def _sphere(n=40, r=12):
    """A solid sphere occupancy volume (uint8 0/255) — a clean marching-cubes target."""
    zz, yy, xx = np.mgrid[:n, :n, :n]
    c = n // 2
    vol = ((xx - c) ** 2 + (yy - c) ** 2 + (zz - c) ** 2 < r * r).astype(np.uint8) * 255
    return vol


def _mesh_from_glb(glb: bytes):
    return trimesh.load(io.BytesIO(glb), file_type="glb", force="mesh")


def test_volume_to_glb_tiff_stack(tmp_path):
    p = tmp_path / "sphere.tif"
    tifffile.imwrite(str(p), _sphere())  # 3-D array → multipage TIFF
    glb = volume_to_glb(str(p))
    m = _mesh_from_glb(glb)
    assert len(m.faces) > 0


def test_volume_to_glb_nifti(tmp_path):
    nib = pytest.importorskip("nibabel")
    p = tmp_path / "sphere.nii"
    nib.save(nib.Nifti1Image(_sphere().astype(np.float32), affine=np.eye(4)), str(p))
    glb = volume_to_glb(str(p))
    assert len(_mesh_from_glb(glb).faces) > 0


def test_volume_to_glb_explicit_threshold(tmp_path):
    p = tmp_path / "sphere.tif"
    tifffile.imwrite(str(p), _sphere())
    glb = volume_to_glb(str(p), threshold=128.0)
    assert len(_mesh_from_glb(glb).faces) > 0


def test_volume_to_glb_decimates(tmp_path):
    p = tmp_path / "sphere.tif"
    tifffile.imwrite(str(p), _sphere(n=64, r=22))
    glb = volume_to_glb(str(p), max_faces=2000)
    assert 0 < len(_mesh_from_glb(glb).faces) <= 2200  # decimated near the budget


def test_volume_to_glb_empty_raises(tmp_path):
    p = tmp_path / "empty.tif"
    tifffile.imwrite(str(p), np.zeros((20, 20, 20), dtype=np.uint8))  # no contrast → no surface
    with pytest.raises(VolumeConvertError):
        volume_to_glb(str(p))


def test_volume_to_glb_unsupported_format(tmp_path):
    p = tmp_path / "x.xyz"
    p.write_text("not a volume")
    with pytest.raises(VolumeConvertError):
        volume_to_glb(str(p))
