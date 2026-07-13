# tests/test_semantic_generator_filter.py
"""enumerate_semantic_work(generators=[...]) narrows the work list to named generators.

Motivation: promoting a newly-generated model means gating only ITS outputs. Without a filter
the enumerator returns every un-scored output in the DB (163 in the live preview DB when only
51 needed gating), and each one costs a headless turntable render plus a VLM call.
"""

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.semantic import enumerate_semantic_work


def setup_module(_m):
    init_db()


def _gen(db, slug):
    gen = db.query(Generator).filter_by(slug=slug).one_or_none()
    if gen is None:
        gen = Generator(slug=slug, name=slug, kind="model", paradigm="image_recon")
        db.add(gen)
        db.flush()
    return gen


def _output(db, cat, gen):
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="p.glb", is_gold=False)
    db.add(out)
    db.flush()
    return out


def test_generators_filter_selects_only_named_generators():
    with SessionLocal() as db:
        cat = db.query(Category).filter_by(slug="sem-genfilter").one_or_none()
        if cat is None:
            cat = Category(slug="sem-genfilter", name="Solanum lycopersicum")
            db.add(cat)
            db.flush()
        wanted = _gen(db, "genfilter:wanted")
        other = _gen(db, "genfilter:other")
        w_out = _output(db, cat, wanted)
        o_out = _output(db, cat, other)
        db.flush()

        ids = {w["output_id"] for w in enumerate_semantic_work(db, generators=["genfilter:wanted"])}
        assert w_out.id in ids
        assert o_out.id not in ids

        # no filter => unchanged behavior, both are enumerated
        all_ids = {w["output_id"] for w in enumerate_semantic_work(db)}
        assert {w_out.id, o_out.id} <= all_ids

        db.rollback()


def test_unknown_generator_slug_yields_empty_work_not_everything():
    """A typo'd slug must NOT silently fall back to 'score the whole DB'."""
    with SessionLocal() as db:
        assert enumerate_semantic_work(db, generators=["no-such-generator"]) == []
