# tests/test_completeness_batch.py
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task, TraitRubric, Completeness
from app.completeness import enumerate_completeness_work, score_outputs


def setup_module(_m):
    init_db()


def _seed(db):
    # Get-or-create the Category/Generator: this module's DB is shared across the whole
    # pytest run (no per-test rollback here), and both slugs are UNIQUE, so a second call
    # (from a second test function) must reuse the existing rows rather than re-insert them.
    cat = db.query(Category).filter_by(slug="pine-batch-test").one_or_none()
    if cat is None:
        cat = Category(slug="pine-batch-test", name="Pinus sylvestris")
        db.add(cat)
        db.flush()
    gen = db.query(Generator).filter_by(slug="gen-batch-test").one_or_none()
    if gen is None:
        gen = Generator(slug="gen-batch-test", name="gen-batch-test", paradigm="")
        db.add(gen)
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


def test_score_outputs_fail_loud_continues_past_a_raising_output():
    with SessionLocal() as db:
        tid, oid1 = _seed(db)
        # add a second output on the same task. Read generator_id off the ModelOutput row
        # directly (not via task.outputs[0]) -- SessionLocal has expire_on_commit=False, and
        # touching task.outputs here would cache a stale 1-item collection on the identity-
        # mapped Task object, which enumerate_completeness_work's later db.get(Task, tid)
        # would then reuse -- silently hiding out2 from the batch.
        from app.models import ModelOutput

        gen_id = db.get(ModelOutput, oid1).generator_id
        out2 = ModelOutput(task_id=tid, generator_id=gen_id, asset_path="p2.glb")
        db.add(out2)
        db.flush()
        oid2 = out2.id
        db.commit()

        work = enumerate_completeness_work(db, [tid])

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

        def sheet_for(oid):
            if oid == oid1:
                raise RuntimeError("render failed")
            return b"\x89PNG"

        summary = score_outputs(
            db, work, client=_FakeClient(), sheet_for=sheet_for, scorer_version="t2"
        )
        db.commit()
        assert summary["errors"] == 1
        assert summary["scored"] == 1
        assert any(f["output_id"] == oid1 for f in summary["failures"])
        row = db.query(Completeness).filter_by(output_id=oid2).one()
        assert row.category == "complete"
