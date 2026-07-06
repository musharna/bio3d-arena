import json

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from scripts.disposition_rose_soybean import plan_disposition


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
        import datetime as dt

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
