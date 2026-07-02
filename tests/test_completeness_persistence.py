# tests/test_completeness_persistence.py

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.completeness import upsert_completeness
from app import service


def setup_module(_m):
    init_db()


def _seed_output(db) -> int:
    cat = Category(slug="tomato-comp-test", name="Solanum lycopersicum")
    gen = Generator(slug="gen-comp-test", name="gen-comp-test", paradigm="")
    db.add_all([cat, gen])
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="x.glb")
    db.add(out)
    db.flush()
    return out.id


def test_upsert_is_one_row_per_output_and_overwrites():
    with SessionLocal() as db:
        oid = _seed_output(db)
        upsert_completeness(
            db,
            oid,
            category="fragment",
            score=0.0,
            checklist={"organs_present": [], "note": "blob"},
            judge_model="m",
            scorer_version="v1",
        )
        db.commit()
        upsert_completeness(
            db,
            oid,
            category="complete",
            score=1.0,
            checklist={"organs_present": [], "note": "ok"},
            judge_model="m",
            scorer_version="v1",
        )
        db.commit()
        rows = [r for r in service.completeness_rows(db) if r["output_id"] == oid]
        assert len(rows) == 1
        assert rows[0]["category"] == "complete"
        assert rows[0]["score"] == 1.0
