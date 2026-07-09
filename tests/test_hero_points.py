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


def test_largest_component_isolates_the_bigger_cluster():
    # two well-separated blobs — a multi-object recon (e.g. a mushroom cluster)
    rng = np.random.default_rng(1)
    big = rng.normal([0, 0, 0], 0.05, (800, 3))
    small = rng.normal([5, 0, 0], 0.05, (200, 3))  # far away -> its own component
    keep = hp.largest_component(np.vstack([big, small]), eps_mult=6.0)
    assert 700 < len(keep) <= 800  # only the big blob survives
    assert keep[:, 0].max() < 1.0  # the distant small blob (x~5) is gone


def test_prepare_cloud_isolate_main_keeps_one_object():
    # a big blob of triangles + a distant small blob; isolate_main drops the small one
    rng = np.random.default_rng(4)
    big = rng.normal([0, 0, 0], 0.1, (1500, 3))
    small = rng.normal([10, 0, 0], 0.1, (400, 3))
    positions = np.vstack([big, small])
    triangles = np.vstack([rng.integers(0, 1500, (3000, 3)), rng.integers(1500, 1900, (800, 3))])
    cloud = np.array(hp.prepare_cloud(positions, triangles, n=8000, isolate_main=True, seed=5))
    # after isolating + normalizing, all points sit in the unit box around the big blob
    assert abs(float(np.abs(cloud).max()) - 1.0) < 1e-6
    assert len(cloud) > 0


def test_crop_base_removes_low_end_and_keeps_cap():
    # a dense wide cap at high Y + a sparse stem/foot descending to low Y
    rng = np.random.default_rng(6)
    cap = np.column_stack(
        [rng.uniform(-1, 1, 2000), 1.0 + rng.normal(0, 0.02, 2000), rng.uniform(-1, 1, 2000)]
    )
    stem = np.column_stack(
        [rng.normal(0, 0.1, 600), rng.uniform(-1, 0.8, 600), rng.normal(0, 0.1, 600)]
    )
    pts = np.vstack([cap, stem])
    ylo, yhi = pts[:, 1].min(), pts[:, 1].max()
    out = hp.crop_base(pts, frac=0.2)
    assert out[:, 1].min() >= ylo + 0.2 * (yhi - ylo) - 1e-9  # bottom 20% gone
    assert (out[:, 1] > 0.9).sum() > 1800  # dense cap preserved


@pytest.mark.skipif(
    not os.path.exists(os.path.join(str(config.ASSET_DIR), "gt", "zea_mays.glb")),
    reason="GT asset bundle not present in this data dir",
)
def test_points_glb_parses_real_gt_scan():
    data = open(os.path.join(str(config.ASSET_DIR), "gt", "zea_mays.glb"), "rb").read()
    pts = hp.points_arrays(data)
    assert pts.ndim == 2 and pts.shape[1] == 3
    assert len(pts) > 1000  # a real dense scan
