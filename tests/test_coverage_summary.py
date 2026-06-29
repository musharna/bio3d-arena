"""service.coverage_summary: per-generator + per-task coverage / vote-count disclosure."""

from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    Metric,
    ModelOutput,
    Task,
    TaskDifficulty,
    Vote,
)


def setup_module(_m):
    init_db()


def _clear(db):
    db.query(Vote).filter(Vote.session_id == "cov-sess").delete(synchronize_session=False)
    db.query(Comparison).filter(Comparison.session_id == "cov-sess").delete(
        synchronize_session=False
    )
    db.query(Metric).filter(
        Metric.output_id.in_(db.query(ModelOutput.id).filter(ModelOutput.asset_path.like("cov/%")))
    ).delete(synchronize_session=False)
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("cov/%")).delete(
        synchronize_session=False
    )
    db.query(TaskDifficulty).filter(
        TaskDifficulty.task_id.in_(db.query(Task.id).filter(Task.title == "cov-task"))
    ).delete(synchronize_session=False)
    db.query(Task).filter(Task.title == "cov-task").delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("cov-g%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="cov-cat").delete(synchronize_session=False)
    db.commit()


def _gen(db, slug, name, kind="model"):
    g = Generator(slug=slug, name=name, kind=kind)
    db.add(g)
    db.flush()
    return g


def _out(db, task, gen, i, *, source="api:fal:trellis", n_cmp=0, is_gold=False, meta="{}"):
    o = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        asset_path=f"cov/{gen.slug}-{i}.glb",
        asset_format="glb",
        source=source,
        n_comparisons=n_cmp,
        is_gold=is_gold,
        meta_json=meta,
    )
    db.add(o)
    db.flush()
    return o


def test_coverage_summary_generators_and_tasks():
    with SessionLocal() as db:
        _clear(db)
        cat = Category(slug="cov-cat", name="CovCat")
        db.add(cat)
        db.flush()
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        task = Task(category_id=cat.id, title="cov-task", prompt="p")
        db.add(task)
        db.flush()
        db.add(TaskDifficulty(task_id=task.id, tier="hard"))

        # firm generator: 2 outputs, 40 total votes (>= threshold)
        gfirm = _gen(db, "cov-gfirm", "Firm")
        ofirm = _out(db, task, gfirm, 0, n_cmp=25)
        _out(db, task, gfirm, 1, n_cmp=15)
        # provisional generator: 1 output, 3 votes
        gprov = _gen(db, "cov-gprov", "Prov")
        oprov = _out(db, task, gprov, 0, n_cmp=3)
        # reference-scan generator: excluded from Mode-A
        gscan = _gen(db, "cov-gscan", "Scan")
        _out(db, task, gscan, 0, source="rose-x", n_cmp=99)
        # gold-only generator: must NOT appear at all
        ggold = _gen(db, "cov-ggold", "Gold")
        _out(db, task, ggold, 0, is_gold=True, n_cmp=5)
        # Mode-B signal: a Metric on one output
        db.add(Metric(output_id=ofirm.id, chamfer=0.1))
        # one decisive Mode-A vote on a non-gold comparison for this task
        cmp = Comparison(
            task_id=task.id,
            output_a_id=ofirm.id,
            output_b_id=oprov.id,
            criterion_id=crit.id,
            session_id="cov-sess",
            is_gold=False,
        )
        db.add(cmp)
        db.flush()
        db.add(Vote(comparison_id=cmp.id, winner="a", session_id="cov-sess"))
        db.commit()

        summ = service.coverage_summary(db)
        gens = {g["generator"]: g for g in summ["generators"]}

        # gold-only generator excluded entirely
        assert "Gold" not in gens
        # firm vs provisional by total votes
        assert gens["Firm"]["votes"] == 40 and gens["Firm"]["confidence"] == "firm"
        assert gens["Prov"]["votes"] == 3 and gens["Prov"]["confidence"] == "provisional"
        assert gens["Firm"]["tasks"] == 1 and gens["Firm"]["outputs"] == 2
        # reference scan present but flagged excluded from Mode-A
        assert gens["Scan"]["excluded_from_mode_a"] is True
        assert gens["Firm"]["excluded_from_mode_a"] is False
        # sorted by votes desc
        assert summ["generators"][0]["votes"] >= summ["generators"][-1]["votes"]

        trow = next(t for t in summ["tasks"] if t["task"] == "cov-task")
        assert trow["category"] == "CovCat" and trow["tier"] == "hard"
        # non-gold outputs only: firm(2) + prov(1) + scan(1) = 4; generators = 3 (gold excluded)
        assert trow["outputs"] == 4 and trow["generators"] == 3
        assert trow["mode_a_votes"] == 1
        assert trow["has_mode_b"] is True
