from __future__ import annotations

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty, tier_scorecard
from app.models import (
    Category,
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
    db.query(TaskDifficulty).delete()
    db.query(Metric).filter(Metric.detail == "td3").delete(synchronize_session=False)
    db.query(OrganMetric).filter(OrganMetric.detail == "td3").delete(synchronize_session=False)
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
        # Two scored outputs in the HARD task; one in the UNTIERED task.
        o1 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/1.glb")
        o2 = ModelOutput(task_id=hard.id, generator_id=gen.id, asset_path="td3/2.glb")
        o3 = ModelOutput(task_id=untiered.id, generator_id=gen.id, asset_path="td3/3.glb")
        db.add_all([o1, o2, o3])
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
        assert r["n_outputs"] == 2 and r["n_scored"] == 2
        assert abs(r["mean_chamfer"] - 0.3) < 1e-9
        assert abs(r["mean_fscore"] - 0.7) < 1e-9
        assert abs(r["mean_structural"] - 0.5) < 1e-9  # only o1 has an OrganMetric
        assert abs(r["species_pass_rate"] - 0.5) < 1e-9  # 1 PASS of 2

        unt_rows = [r for r in by_tier["untiered"]["rows"] if r["generator"] == "Gen"]
        assert len(unt_rows) == 1
        assert unt_rows[0]["n_outputs"] == 1 and unt_rows[0]["n_scored"] == 0
        assert unt_rows[0]["mean_chamfer"] is None  # unscored → None, not 0
        assert by_tier["easy"]["rows"] == []  # empty tier present, honest
