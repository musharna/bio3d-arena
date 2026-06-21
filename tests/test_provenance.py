from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_model_output_provenance_defaults():
    db = SessionLocal()
    try:
        cat = Category(slug="c-prov", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-prov", prompt="p")
        gen = Generator(slug="g-prov", name="G")
        db.add_all([task, gen])
        db.flush()
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.flush()
        assert out.source == "bio3d-arena"
        assert out.license is None
        assert out.attribution is None
        assert out.external_url is None
    finally:
        db.close()
