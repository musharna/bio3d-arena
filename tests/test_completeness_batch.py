# tests/test_completeness_batch.py
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task, TraitRubric, Completeness
from app.completeness import enumerate_completeness_work, score_outputs


def setup_module(_m):
    init_db()


def _seed(db):
    cat = Category(slug="pine-batch-test", name="Pinus sylvestris")
    gen = Generator(slug="gen-batch-test", name="gen-batch-test", paradigm="")
    db.add_all([cat, gen])
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    db.add(TraitRubric(task_id=task.id, taxon="Pinus sylvestris", traits_json="[]"))
    out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="p.glb")
    db.add(out)
    db.flush()
    return task.id, out.id


def test_enumerate_and_score_writes_completeness():
    with SessionLocal() as db:
        tid, oid = _seed(db)
        db.commit()
        work = enumerate_completeness_work(db, [tid])
        assert {"output_id": oid, "taxon": "Pinus sylvestris"} in work

        class _FakeClient:
            def __init__(self):
                self.messages = self

            def create(self, **kw):
                class B:
                    type = "tool_use"
                    name = "record_completeness"
                    input = {
                        "organs_present": [
                            {"key": "vegetative_axis", "status": "present"},
                            {"key": "foliage", "status": "present"},
                            {"key": "reproductive_cone", "status": "absent"},
                        ],
                        "note": "ok",
                    }

                class R:
                    content = [B()]

                return R()

        summary = score_outputs(
            db, work, client=_FakeClient(), sheet_for=lambda oid: b"\x89PNG", scorer_version="t1"
        )
        db.commit()
        assert summary["scored"] == 1
        row = db.query(Completeness).filter_by(output_id=oid).one()
        assert row.category == "complete"
        assert row.score == 1.0
