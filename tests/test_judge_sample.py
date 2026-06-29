"""Bounded-sample judge enumeration: connected, capped, excludes Mode-A outputs."""

from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Category, Criterion, Generator, JudgeVote, ModelOutput, Task


def setup_module(_m):
    init_db()


def _seed(db):
    db.query(JudgeVote).delete()
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("smp/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title == "smp-task").delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("smp-g%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="smp-cat").delete(synchronize_session=False)
    db.commit()
    cat = Category(slug="smp-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    task = Task(category_id=cat.id, title="smp-task", prompt="p")  # active by default
    db.add(task)
    db.flush()
    ai_ids = []
    for i in range(6):
        g = Generator(slug=f"smp-gai{i}", name=f"AI{i}")
        db.add(g)
        db.flush()
        o = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            asset_path=f"smp/ai{i}.glb",
            asset_format="glb",
            source="api:fal:trellis",
        )
        db.add(o)
        db.flush()
        ai_ids.append(o.id)
    # one reference scan + one untextured → must be excluded from the sample
    gref = Generator(slug="smp-gref", name="ref")
    gunt = Generator(slug="smp-gunt", name="unt")
    db.add_all([gref, gunt])
    db.flush()
    ref = ModelOutput(
        task_id=task.id,
        generator_id=gref.id,
        asset_path="smp/ref.glb",
        asset_format="glb",
        source="rose-x",
    )
    unt = ModelOutput(
        task_id=task.id,
        generator_id=gunt.id,
        asset_path="smp/unt.glb",
        asset_format="glb",
        source="bio3d-arena",
        meta_json='{"untextured": true}',
    )
    db.add_all([ref, unt])
    db.commit()
    return task, sorted(ai_ids), ref.id, unt.id


def test_enumerate_sample_connected_capped_and_excludes_mode_a():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, ai_ids, ref_id, unt_id = _seed(db)
        items = jv.enumerate_sample(db, [task.id], criterion_slug="overall", per_output_k=2)

        # two ordered rows per logical pair
        assert len(items) % 2 == 0
        pairs = {tuple(sorted((it["output_a_id"], it["output_b_id"]))) for it in items}
        appeared = {x for p in pairs for x in p}

        # exclusion: reference-scan + untextured outputs never appear
        assert ref_id not in appeared and unt_id not in appeared
        # connectivity: every AI output is covered
        assert appeared == set(ai_ids)
        # capped well below the full grid C(6,2)=15 (circulant n=6,k=2 → 12 pairs)
        assert len(pairs) == 12
        assert len(items) == 24  # 12 pairs × 2 orders
        # everything is the overall criterion, multi4 condition
        assert all(it["criterion_slug"] == "overall" for it in items)
        assert all(it["condition"] == jv.GRID_CONDITION for it in items)


def test_enumerate_sample_skips_tiny_tasks():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        # a non-existent task id yields no work (no crash)
        assert jv.enumerate_sample(db, [10**9], criterion_slug="overall") == []
