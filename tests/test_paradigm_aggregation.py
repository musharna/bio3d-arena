from __future__ import annotations

import uuid

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
    g = Generator(
        slug=f"agg-{paradigm}-{uuid.uuid4().hex}", name="g", kind="model", paradigm=paradigm
    )
    db.add(g)
    db.flush()
    o = ModelOutput(task_id=db._task_id, generator_id=g.id, asset_path="x.glb", is_gold=False)
    db.add(o)
    db.flush()
    return g, o


def test_cross_paradigm_comparison_excluded_from_matches():
    with SessionLocal() as db:
        cat = Category(slug=f"c{uuid.uuid4().hex}", name="c")
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
        matches, _groups = service._matches_for_scope(db, crit.id, None)
        pairs = set(matches)
        assert (g1.id, g3.id) in pairs  # within-paradigm kept
        assert (g1.id, g2.id) not in pairs  # cross-paradigm dropped
        assert (g2.id, g1.id) not in pairs


def test_same_generator_comparison_excluded_from_matches():
    """A comparison whose two outputs share a generator ("TRELLIS vs TRELLIS") must never
    reach Bradley-Terry, even when it carries a decisive vote — a model cannot beat itself.
    Historic self-pairs stay in the DB (audit trail); they are simply inert to the fit."""
    with SessionLocal() as db:
        cat = Category(slug=f"c{uuid.uuid4().hex}", name="c")
        db.add(cat)
        db.flush()
        crit = Criterion(slug=f"selfpair-{uuid.uuid4().hex[:8]}", name="Overall")
        db.add(crit)
        db.flush()
        t = Task(category_id=cat.id, title="t", prompt="p")
        db.add(t)
        db.flush()
        db._task_id = t.id
        g1, o1 = _mk(db, "image_recon")
        # a SECOND output owned by the SAME generator (common: one model, several outputs)
        o1b = ModelOutput(task_id=t.id, generator_id=g1.id, asset_path="x2.glb", is_gold=False)
        db.add(o1b)
        db.flush()
        g2, o2 = _mk(db, "image_recon")

        for a, b, key in [(o1, o1b, "self1"), (o1, o2, "cross1")]:
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
            db.add(Vote(comparison_id=comp.id, winner="a", session_id=key))  # decisive
            db.flush()
        db.commit()

        matches, groups = service._matches_for_scope(db, crit.id, None)
        assert (g1.id, g1.id) not in set(matches)  # self-match never fed to BT
        assert (g1.id, g2.id) in set(matches)  # the real match survives
        assert matches == [(g1.id, g2.id)]  # and it is the ONLY contribution
        assert len(groups) == len(matches)


def test_same_generator_tie_excluded_from_matches():
    """The tie path splits a vote into BOTH directions; a self-pair tie must not sneak in
    two (G, G) matches."""
    with SessionLocal() as db:
        cat = Category(slug=f"c{uuid.uuid4().hex}", name="c")
        db.add(cat)
        db.flush()
        crit = Criterion(slug=f"selftie-{uuid.uuid4().hex[:8]}", name="Overall")
        db.add(crit)
        db.flush()
        t = Task(category_id=cat.id, title="t", prompt="p")
        db.add(t)
        db.flush()
        db._task_id = t.id
        g1, o1 = _mk(db, "image_recon")
        o1b = ModelOutput(task_id=t.id, generator_id=g1.id, asset_path="x2.glb", is_gold=False)
        db.add(o1b)
        db.flush()
        comp = Comparison(
            task_id=t.id,
            output_a_id=o1.id,
            output_b_id=o1b.id,
            criterion_id=crit.id,
            session_id="selftie",
            is_gold=False,
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="tie", session_id="selftie"))
        db.commit()

        matches, groups = service._matches_for_scope(db, crit.id, None, include_ties=True)
        assert matches == []
        assert groups == []
