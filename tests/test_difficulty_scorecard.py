from __future__ import annotations

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty, tier_scorecard
from app.models import (
    Category,
    Completeness,
    Generator,
    Metric,
    ModelOutput,
    OrganMetric,
    Task,
    TaskDifficulty,
)


def setup_module(_m):
    init_db()


def _clean(db):
    # Delete ALL TaskDifficulty rows — tier_scorecard reads global state, test must own it fully.
    db.query(TaskDifficulty).delete()
    db.query(Metric).filter(Metric.detail == "td3").delete(synchronize_session=False)
    db.query(OrganMetric).filter(OrganMetric.detail == "td3").delete(synchronize_session=False)
    # Completeness.output_id is unique with no ON DELETE cascade; SQLite reuses deleted rowids,
    # so orphaned rows here would collide with a later test's reused output id. Clear them first.
    td3_out_ids = (
        db.query(ModelOutput.id).filter(ModelOutput.asset_path.like("td3/%.glb")).scalar_subquery()
    )
    db.query(Completeness).filter(Completeness.output_id.in_(td3_out_ids)).delete(
        synchronize_session=False
    )
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("td3/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("td3-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("td3-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="td3-cat").delete(synchronize_session=False)
    db.commit()


def test_scorecard_groups_by_tier_and_generator():
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="td3-cat", name="C")
        db.add(cat)
        gen = Generator(slug="td3-g", name="Gen")
        db.add_all([cat, gen])
        db.flush()
        hard = Task(category_id=cat.id, title="td3-hard", prompt="p")
        untiered = Task(category_id=cat.id, title="td3-unt", prompt="p")
        db.add_all([hard, untiered])
        db.flush()
        # Two scored outputs + one gold in the HARD task; one in the UNTIERED task.
        o1 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/1.glb")
        o2 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/2.glb")
        o3 = ModelOutput(task_id=untiered.id, generator_id=gen.id, asset_path="td3/3.glb")
        o4 = ModelOutput(
            task_id=hard.id, generator_id=gen.id, asset_path="td3/gold.glb", is_gold=True
        )
        db.add_all([o1, o2, o3, o4])
        db.flush()
        db.add_all(
            [
                Metric(
                    output_id=o1.id, chamfer=0.2, fscore=0.6, species_verdict="PASS", detail="td3"
                ),
                Metric(
                    output_id=o2.id, chamfer=0.4, fscore=0.8, species_verdict="FAIL", detail="td3"
                ),
                # o3 has no Metric (unscored) → counts toward n_outputs, not n_scored.
                # o4 is gold — has a Metric but must be excluded from scorecard counts.
                Metric(
                    output_id=o4.id, chamfer=0.1, fscore=0.9, species_verdict="PASS", detail="td3"
                ),
                OrganMetric(output_id=o1.id, botanical_fidelity=0.5, detail="td3"),
            ]
        )
        set_task_difficulty(db, hard.id, "hard", "occlusion", commit=False)
        db.commit()

        card = tier_scorecard(db)
        by_tier = {c["tier"]: c for c in card}
        assert [c["tier"] for c in card] == ["easy", "moderate", "hard", "untiered"]

        hard_rows = [r for r in by_tier["hard"]["rows"] if r["generator"] == "Gen"]
        assert len(hard_rows) == 1
        r = hard_rows[0]
        assert r["n_outputs"] == 2 and r["n_scored"] == 2  # gold output excluded → still 2
        assert abs(r["mean_chamfer"] - 0.3) < 1e-9
        assert abs(r["mean_fscore"] - 0.7) < 1e-9
        assert abs(r["mean_structural"] - 0.5) < 1e-9  # only o1 has an OrganMetric
        assert abs(r["species_pass_rate"] - 0.5) < 1e-9  # 1 PASS of 2

        unt_rows = [r for r in by_tier["untiered"]["rows"] if r["generator"] == "Gen"]
        assert len(unt_rows) == 1
        assert unt_rows[0]["n_outputs"] == 1 and unt_rows[0]["n_scored"] == 0
        assert unt_rows[0]["mean_chamfer"] is None  # unscored → None, not 0
        assert unt_rows[0]["mean_structural"] is None  # no OrganMetric for o3
        assert unt_rows[0]["species_pass_rate"] is None  # no verdicts for o3
        # Robust: assert no "Gen" row in easy (guards against unrelated rows from other tests).
        assert all(r["generator"] != "Gen" for r in by_tier["easy"]["rows"])


def test_error_status_metric_not_counted_as_scored():
    # status='error' metrics (failed scoring, null chamfer) count toward n_outputs but not
    # n_scored — same rule as the paradigm grid. Regression for the GT-less expansion taxa.
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="td3-cat", name="C")
        gen = Generator(slug="td3-g", name="Gen")
        db.add_all([cat, gen])
        db.flush()
        hard = Task(category_id=cat.id, title="td3-hard", prompt="p")
        db.add(hard)
        db.flush()
        o_ok = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/ok.glb")
        o_err = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/err.glb")
        db.add_all([o_ok, o_err])
        db.flush()
        db.add_all(
            [
                Metric(
                    output_id=o_ok.id,
                    chamfer=0.2,
                    fscore=0.6,
                    species_verdict="PASS",
                    status="ok",
                    detail="td3",
                ),
                Metric(output_id=o_err.id, status="error", detail="td3"),  # null chamfer/fscore
            ]
        )
        set_task_difficulty(db, hard.id, "hard", "occlusion", commit=False)
        db.commit()

        card = tier_scorecard(db)
        by_tier = {c["tier"]: c for c in card}
        r = next(r for r in by_tier["hard"]["rows"] if r["generator"] == "Gen")
        assert r["n_outputs"] == 2
        assert r["n_scored"] == 1
        assert abs(r["mean_chamfer"] - 0.2) < 1e-9


def test_generator_grid_includes_reference_free_completeness():
    # Same reference-free completeness dimension as the paradigm grid: it populates even where
    # chamfer is null (no GT), which is the point for fungi. Two outputs, no Metric rows.
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="td3-cat", name="C")
        gen = Generator(slug="td3-g", name="Gen")
        db.add_all([cat, gen])
        db.flush()
        hard = Task(category_id=cat.id, title="td3-hard", prompt="p")
        db.add(hard)
        db.flush()
        o1 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/1.glb")
        o2 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/2.glb")
        db.add_all([o1, o2])
        db.flush()
        db.add_all(
            [
                Completeness(output_id=o1.id, category="complete", score=1.0),
                Completeness(output_id=o2.id, category="fragment", score=0.0),
            ]
        )
        set_task_difficulty(db, hard.id, "hard", "occlusion", commit=False)
        db.commit()

        r = next(
            row
            for row in {c["tier"]: c for c in tier_scorecard(db)}["hard"]["rows"]
            if row["generator"] == "Gen"
        )
        assert r["n_scored"] == 0 and r["mean_chamfer"] is None  # objective path empty
        assert r["completeness_n"] == 2
        assert abs(r["mean_completeness"] - 0.5) < 1e-9
        assert abs(r["pct_complete"] - 0.5) < 1e-9
        _clean(db)
