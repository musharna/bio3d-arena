# tests/test_paradigm_tier_scorecard.py
from app.database import SessionLocal, init_db
from app.difficulty import paradigm_tier_scorecard, set_task_difficulty, tier_scorecard
from app.models import (
    Category,
    Completeness,
    Generator,
    Metric,
    ModelOutput,
    Task,
    TaskDifficulty,
)


def setup_module(_m):
    init_db()


def _clean(db):
    # paradigm_tier_scorecard reads global TaskDifficulty → this test must own tier state fully.
    db.query(TaskDifficulty).delete()
    db.query(Metric).filter(Metric.detail == "pts").delete(synchronize_session=False)
    # Completeness.output_id is unique with no ON DELETE cascade + SQLite reuses deleted rowids;
    # clear these rows before the outputs so a later test's reused id can't collide.
    pts_out_ids = (
        db.query(ModelOutput.id).filter(ModelOutput.asset_path.like("pts/%.glb")).scalar_subquery()
    )
    db.query(Completeness).filter(Completeness.output_id.in_(pts_out_ids)).delete(
        synchronize_session=False
    )
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("pts/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("pts-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("pts-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="pts-cat").delete(synchronize_session=False)
    db.commit()


def test_paradigm_grid_groups_by_paradigm_and_tier():
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="pts-cat", name="C")
        g_recon = Generator(slug="pts-recon", name="Recon", paradigm="image_recon")
        g_proc = Generator(slug="pts-proc", name="Proc", paradigm="procedural_llm")
        g_bare = Generator(slug="pts-bare", name="Bare", paradigm="")  # empty → 'unspecified'
        db.add_all([cat, g_recon, g_proc, g_bare])
        db.flush()
        hard = Task(category_id=cat.id, title="pts-hard", prompt="p")
        db.add(hard)
        db.flush()
        o1 = ModelOutput(task_id=hard.id, generator_id=g_recon.id, asset_path="pts/1.glb")
        o2 = ModelOutput(task_id=hard.id, generator_id=g_recon.id, asset_path="pts/2.glb")
        o3 = ModelOutput(task_id=hard.id, generator_id=g_proc.id, asset_path="pts/3.glb")
        o4 = ModelOutput(
            task_id=hard.id, generator_id=g_bare.id, asset_path="pts/4.glb"
        )  # unscored
        db.add_all([o1, o2, o3, o4])
        db.flush()
        db.add_all(
            [
                Metric(
                    output_id=o1.id, chamfer=0.2, fscore=0.6, species_verdict="PASS", detail="pts"
                ),
                Metric(
                    output_id=o2.id, chamfer=0.3, fscore=0.8, species_verdict="PASS", detail="pts"
                ),
                Metric(
                    output_id=o3.id, chamfer=0.1, fscore=0.9, species_verdict="PASS", detail="pts"
                ),
                # o4 has no Metric (unscored) → counts toward n_outputs, not n_scored.
            ]
        )
        set_task_difficulty(db, hard.id, "hard", "occlusion", commit=False)
        db.commit()

        card = paradigm_tier_scorecard(db)
        assert [b["tier"] for b in card] == ["easy", "moderate", "hard", "untiered"]
        hard_b = next(b for b in card if b["tier"] == "hard")
        rows = {r["paradigm"]: r for r in hard_b["rows"]}
        assert rows["image_recon"]["n_outputs"] == 2
        assert rows["image_recon"]["n_scored"] == 2
        assert abs(rows["image_recon"]["mean_chamfer"] - 0.25) < 1e-9
        assert rows["image_recon"]["paradigm_display"] == "Image→3D reconstruction"
        assert rows["image_recon"]["species_pass_rate"] == 1.0
        assert rows["procedural_llm"]["n_outputs"] == 1
        assert rows["unspecified"]["n_outputs"] == 1
        assert rows["unspecified"]["mean_chamfer"] is None  # unscored → None, never zero


def test_error_status_metric_not_counted_as_scored():
    # A metric row with status='error' (failed scoring, e.g. a taxon with no GT mesh) leaves
    # chamfer NULL. It must count toward n_outputs but NOT n_scored — a failed attempt is not
    # a score. Regression for the expansion wave, which produced 24 GT-less error metrics.
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="pts-cat", name="C")
        g_recon = Generator(slug="pts-recon", name="Recon", paradigm="image_recon")
        db.add_all([cat, g_recon])
        db.flush()
        hard = Task(category_id=cat.id, title="pts-hard", prompt="p")
        db.add(hard)
        db.flush()
        o_ok = ModelOutput(task_id=hard.id, generator_id=g_recon.id, asset_path="pts/ok.glb")
        o_err = ModelOutput(task_id=hard.id, generator_id=g_recon.id, asset_path="pts/err.glb")
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
                    detail="pts",
                ),
                Metric(output_id=o_err.id, status="error", detail="pts"),  # null chamfer/fscore
            ]
        )
        set_task_difficulty(db, hard.id, "hard", "occlusion", commit=False)
        db.commit()

        card = paradigm_tier_scorecard(db)
        hard_b = next(b for b in card if b["tier"] == "hard")
        row = {r["paradigm"]: r for r in hard_b["rows"]}["image_recon"]
        assert row["n_outputs"] == 2  # both outputs present
        assert row["n_scored"] == 1  # only the ok metric is a score
        assert abs(row["mean_chamfer"] - 0.2) < 1e-9


def test_paradigm_grid_includes_reference_free_completeness():
    # Completeness is reference-free: it populates even where chamfer is null (no GT mesh) — the
    # whole point for fungi. Two recon outputs (one complete, one fragment), NO Metric rows.
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="pts-cat", name="C")
        g = Generator(slug="pts-recon", name="Recon", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        hard = Task(category_id=cat.id, title="pts-hard", prompt="p")
        db.add(hard)
        db.flush()
        o1 = ModelOutput(task_id=hard.id, generator_id=g.id, asset_path="pts/1.glb")
        o2 = ModelOutput(task_id=hard.id, generator_id=g.id, asset_path="pts/2.glb")
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

        card = paradigm_tier_scorecard(db)
        row = {r["paradigm"]: r for r in next(b for b in card if b["tier"] == "hard")["rows"]}[
            "image_recon"
        ]
        assert row["n_scored"] == 0  # no Metric rows → the objective/chamfer path is empty
        assert row["mean_chamfer"] is None
        # ...but the reference-free completeness dimension is populated
        assert row["completeness_n"] == 2
        assert abs(row["mean_completeness"] - 0.5) < 1e-9
        assert abs(row["pct_complete"] - 0.5) < 1e-9  # 1 of 2 is 'complete'


def test_tier_scorecard_shape_regression():
    # the generator-level scorecard must still return the documented shape
    with SessionLocal() as db:
        card = tier_scorecard(db)
        assert [b["tier"] for b in card] == ["easy", "moderate", "hard", "untiered"]
        assert all(set(b) == {"tier", "rows"} for b in card)
