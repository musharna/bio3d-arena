from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    Vote,
)


def setup_module(_m):
    init_db()


def _mk(db, paradigm):
    g = Generator(slug=f"agg-{paradigm}-{id(object())}", name="g", kind="model", paradigm=paradigm)
    db.add(g)
    db.flush()
    o = ModelOutput(task_id=db._task_id, generator_id=g.id, asset_path="x.glb", is_gold=False)
    db.add(o)
    db.flush()
    return g, o


def test_cross_paradigm_comparison_excluded_from_matches():
    with SessionLocal() as db:
        cat = Category(slug=f"c{id(object())}", name="c")
        db.add(cat)
        db.flush()
        crit = db.execute(
            __import__("sqlalchemy").select(Criterion).where(Criterion.slug == "overall")
        ).scalars().first() or Criterion(slug="overall", name="Overall")
        if crit.id is None:
            db.add(crit)
            db.flush()
        t = Task(category_id=cat.id, title="t", prompt="p")
        db.add(t)
        db.flush()
        db._task_id = t.id
        g1, o1 = _mk(db, "image_recon")
        g2, o2 = _mk(db, "procedural_llm")  # different paradigm
        g3, o3 = _mk(db, "image_recon")  # same as g1
        # cross-paradigm comparison (o1 vs o2) + within-paradigm (o1 vs o3)
        for a, b, key in [(o1, o2, "x1"), (o1, o3, "x2")]:
            comp = Comparison(
                task_id=t.id,
                output_a_id=a.id,
                output_b_id=b.id,
                criterion_id=crit.id,
                session_id=key,
                is_gold=False,
            )
            db.add(comp)
            db.flush()
            db.add(Vote(comparison_id=comp.id, winner="a", session_id=key))
            db.flush()
        db.commit()
        matches = service._matches_for_scope(db, crit.id, None)
        pairs = set(matches)
        assert (g1.id, g3.id) in pairs  # within-paradigm kept
        assert (g1.id, g2.id) not in pairs  # cross-paradigm dropped
        assert (g2.id, g1.id) not in pairs
