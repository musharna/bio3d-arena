"""Metric table roundtrip — the Mode-B objective recon-accuracy store."""

from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Category, Generator, Metric, ModelOutput, Task


def setup_module(_module):
    init_db()


def test_metric_row_roundtrips():
    db = SessionLocal()
    try:
        cat = Category(slug="plants-test-m", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="recon-t", prompt="p")
        gen = Generator(slug="trellis-test-m", name="TRELLIS")
        db.add_all([task, gen])
        db.flush()
        out = ModelOutput(
            task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
        )
        db.add(out)
        db.flush()
        m = Metric(
            output_id=out.id,
            chamfer=0.012,
            nearest_shape_distance=0.012,
            nearest_gt_idx=2,
            fscore=0.81,
            tau=0.01,
            coverage=0.74,
            species_verdict="PASS",
            gt_band_lo=0.008,
            gt_band_hi=0.02,
            point_count=16384,
            icp_seed=0,
            scorer_version="agrigen-eval@abc123",
            gt_version_hash="sha256:deadbeef",
            status="ok",
            detail="",
        )
        db.add(m)
        db.commit()
        got = db.query(Metric).filter(Metric.output_id == out.id).one()
        assert got.chamfer == 0.012
        assert got.species_verdict == "PASS"
        assert got.gt_version_hash == "sha256:deadbeef"
    finally:
        db.close()
