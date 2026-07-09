"""Bake-time helpers that turn GT scans / reconstruction meshes into the home-hero
point clouds. Pure-geometry functions are unit-tested here; the GLB parsers get a
real-execution check against a shipped asset (skipped if the data dir isn't present)."""

import os

import numpy as np
import pytest

from app import config, hero_points as hp


def test_normalize_centers_and_scales_to_unit():
    pts = np.array([[0, 0, 0], [2, 0, 0], [0, 4, 0], [0, 0, 6]], float)
    n = hp.normalize(pts)
    assert abs(float(np.abs(n).max()) - 1.0) < 1e-9  # fills the unit box
    center = (n.max(0) + n.min(0)) / 2
    assert np.allclose(center, 0.0, atol=1e-9)  # centred on origin


def test_remove_outliers_strips_isolated_point():
    rng = np.random.default_rng(0)
    cluster = rng.normal(0.0, 0.1, (500, 3))
    outlier = np.array([[10.0, 10.0, 10.0]])
    pts = np.vstack([cluster, outlier])
    clean = hp.remove_outliers(pts)
    assert len(clean) < len(pts)
    assert not np.any(np.all(np.isclose(clean, outlier), axis=1)), "stray point survived"


def test_sample_surface_stays_within_mesh_bbox_and_hits_count():
    # unit square in the XY plane, two triangles
    positions = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    pts = hp.sample_surface(positions, triangles, n=1000, seed=1)
    assert len(pts) == 1000
    assert -1e-9 <= pts[:, 0].min() and pts[:, 0].max() <= 1 + 1e-9
    assert -1e-9 <= pts[:, 1].min() and pts[:, 1].max() <= 1 + 1e-9
    assert np.allclose(pts[:, 2], 0.0)


def test_prepare_cloud_is_clean_normalized_and_capped():
    rng = np.random.default_rng(2)
    positions = rng.normal(0, 1, (2000, 3))
    triangles = rng.integers(0, 2000, (4000, 3))
    cloud = hp.prepare_cloud(positions, triangles, n=6000, target=5000, seed=3)
    assert len(cloud) <= 5000
    assert abs(float(np.abs(np.array(cloud)).max()) - 1.0) < 1e-6
    # JSON-ready: list of 3-float lists
    assert all(len(p) == 3 for p in cloud)


@pytest.mark.skipif(
    not os.path.exists(os.path.join(str(config.ASSET_DIR), "gt", "zea_mays.glb")),
    reason="GT asset bundle not present in this data dir",
)
def test_points_glb_parses_real_gt_scan():
    data = open(os.path.join(str(config.ASSET_DIR), "gt", "zea_mays.glb"), "rb").read()
    pts = hp.points_arrays(data)
    assert pts.ndim == 2 and pts.shape[1] == 3
    assert len(pts) > 1000  # a real dense scan
