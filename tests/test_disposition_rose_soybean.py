import datetime as dt
import json

import pytest

from app import config
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from scripts.disposition_rose_soybean import apply_disposition, main, plan_disposition


def setup_module(_m):
    init_db()


def test_plan_unhides_original_input_recon_and_hides_clean_swap():
    with SessionLocal() as db:
        cat = Category(slug="plants-d", name="P")
        g = Generator(slug="d-recon", name="r", kind="model", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        t = Task(
            category_id=cat.id,
            title="Rosa — single-image → 3D reconstruction",
            prompt="p",
            active=True,
        )
        db.add(t)
        db.flush()

        good = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="good.glb",
            source="api:fal:trellis",
            meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}),
            hidden_at=dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc),
        )  # currently hidden
        weak = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="weak.glb",
            source="api:fal:trellis",
            meta_json=json.dumps({"input_image": "reference/rose_ref_clean.jpg"}),
        )  # visible
        db.add_all([good, weak])
        db.flush()

        plan = plan_disposition(db)
        assert good.id in plan["unhide"]
        assert weak.id in plan["hide"]
        db.rollback()


def test_apply_disposition_flips_hidden_at():
    with SessionLocal() as db:
        cat = Category(slug="plants-apply", name="P")
        g = Generator(slug="apply-recon", name="r", kind="model", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        t = Task(
            category_id=cat.id,
            title="Rosa — single-image → 3D reconstruction",
            prompt="p",
            active=True,
        )
        db.add(t)
        db.flush()

        good = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="good-apply.glb",
            source="api:fal:trellis",
            meta_json=json.dumps({"input_image": "reference/rose_ref.jpg"}),
            hidden_at=dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc),
        )  # currently hidden -> should be un-hidden
        weak = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="weak-apply.glb",
            source="api:fal:trellis",
            meta_json=json.dumps({"input_image": "reference/rose_ref_clean.jpg"}),
            hidden_at=None,
        )  # currently visible -> should be hidden
        db.add_all([good, weak])
        db.flush()

        plan = plan_disposition(db)
        apply_disposition(db, plan)

        db.refresh(good)
        db.refresh(weak)
        assert good.hidden_at is None
        assert weak.hidden_at is not None

        # apply_disposition commits (unlike the plan-only test above), so undo explicitly
        # rather than relying on rollback -- keeps the shared test DB clean for later tests.
        db.delete(good)
        db.delete(weak)
        db.delete(t)
        db.delete(g)
        db.delete(cat)
        db.commit()


def test_main_refuses_real_study_db(monkeypatch):
    monkeypatch.setattr(
        config, "DATABASE_URL", "sqlite:////home/u/bio3d-arena/data/study/arena-study.db"
    )
    with pytest.raises(SystemExit):
        main()
