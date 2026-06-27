# tests/test_generate_multiview_recon.py
from __future__ import annotations

import json

import trimesh
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task

from scripts.generate_multiview_recon import run_subject


def setup_module(_m):
    init_db()


PINE = "Pinus sylvestris — single-image → 3D reconstruction"


def _seed(db):
    db.query(ModelOutput).filter(ModelOutput.source.like("recon:%mvt%")).delete(
        synchronize_session=False
    )
    db.query(Task).filter_by(title=PINE).delete(synchronize_session=False)
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="P")
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=PINE, prompt="p"))
    db.commit()


def _box():
    return trimesh.creation.box().export(file_type="glb")


def test_run_subject_nvs_then_multiview(tmp_path):
    with SessionLocal() as db:
        _seed(db)
        calls = {}

        def fake_nvs(image_bytes, *, api_key):
            calls["nvs"] = True
            return [b"v%d" % i for i in range(6)]

        def fake_mv(views, *, api_key):
            calls["mv_n"] = len(views)
            return _box()

        mv = {"recon:trellis-mv-mvt": (fake_mv, "FAL_KEY", "TRELLIS mv")}
        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}  # any existing ref file
        res = run_subject(
            db,
            subj,
            env={"REPLICATE_API_TOKEN": "r", "FAL_KEY": "f"},
            nvs_fn=fake_nvs,
            mv_providers=mv,
            views_dir=tmp_path,
        )
        assert calls["nvs"] and calls["mv_n"] == 6
        assert res["n_views"] == 6 and res["recon"]["generated"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.source == "recon:trellis-mv-mvt"))
            .scalars()
            .one()
        )
        assert json.loads(out.meta_json)["modality"] == "multiview"
        # views were cached to disk
        assert len(list(tmp_path.glob("*.png"))) == 6


def test_run_subject_skips_when_nvs_too_few():
    with SessionLocal() as db:
        _seed(db)

        def fake_nvs(image_bytes, *, api_key):
            return [b"only-one"]

        subj = {"ref": "reference/arabidopsis_ref.jpg", "task_title": PINE}
        res = run_subject(
            db,
            subj,
            env={"REPLICATE_API_TOKEN": "r", "FAL_KEY": "f"},
            nvs_fn=fake_nvs,
            mv_providers={"recon:x": (lambda *a, **k: _box(), "FAL_KEY", "x")},
            views_dir=None,
        )
        assert "skipped" in res
