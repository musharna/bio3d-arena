# tests/test_recon_reliability.py
from app.completeness import recon_reliability_flags
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Completeness,
    Generator,
    ModelOutput,
    Task,
    TaskDifficulty,
    TraitRubric,
)


def setup_module(_m):
    init_db()


def _clean(db):
    seen_ids = db.query(Task.id).filter(Task.title.like("rr-%")).scalar_subquery()
    out_ids = db.query(ModelOutput.id).filter(ModelOutput.task_id.in_(seen_ids)).scalar_subquery()
    db.query(Completeness).filter(Completeness.output_id.in_(out_ids)).delete(
        synchronize_session=False
    )
    db.query(ModelOutput).filter(ModelOutput.task_id.in_(seen_ids)).delete(
        synchronize_session=False
    )
    db.query(TraitRubric).filter(TraitRubric.taxon.like("RR %")).delete(synchronize_session=False)
    db.query(TaskDifficulty).filter(TaskDifficulty.task_id.in_(seen_ids)).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("rr-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("rr-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="rr-cat").delete(synchronize_session=False)
    db.commit()


def _taxon(db, cat, name, recon_scores, text_scores):
    """Make a task for `name` with a rubric, and completeness rows at the given recon/text scores."""
    t = Task(category_id=cat.id, title=f"rr-{name}", prompt="p")
    db.add(t)
    db.flush()
    db.add(TraitRubric(task_id=t.id, taxon=f"RR {name}"))
    g_recon = Generator(slug=f"rr-{name}-r", name="R", paradigm="image_recon")
    g_text = Generator(slug=f"rr-{name}-t", name="T", paradigm="text_native")
    db.add_all([g_recon, g_text])
    db.flush()
    for i, s in enumerate(recon_scores):
        o = ModelOutput(task_id=t.id, generator_id=g_recon.id, asset_path=f"rr/{name}-r{i}.glb")
        db.add(o)
        db.flush()
        db.add(Completeness(output_id=o.id, category="x", score=s))
    for i, s in enumerate(text_scores):
        o = ModelOutput(task_id=t.id, generator_id=g_text.id, asset_path=f"rr/{name}-t{i}.glb")
        db.add(o)
        db.flush()
        db.add(Completeness(output_id=o.id, category="x", score=s))
    return t


def test_recon_reliability_excludes_hidden_outputs():
    """Outputs withdrawn from the arena (hidden_at set) — e.g. recon from a since-replaced input
    photo — must not drag the taxon's recon mean. Hiding the bad-input batch + regenerating from a
    clean input should let the flag clear."""
    import datetime as dt

    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="rr-cat", name="C")
        db.add(cat)
        db.flush()
        # recon = two hidden zeros (old cluttered-input batch) + two visible ones (clean regen)
        t = _taxon(db, cat, "tom", recon_scores=[0.0, 0.0, 1.0, 1.0], text_scores=[1.0, 1.0])
        recon_outs = (
            db.query(ModelOutput)
            .join(Generator, ModelOutput.generator_id == Generator.id)
            .filter(ModelOutput.task_id == t.id, Generator.paradigm == "image_recon")
            .order_by(ModelOutput.id)
            .all()
        )
        # withdraw the two zero-scoring outputs
        for o in recon_outs[:2]:
            o.hidden_at = dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc)
        db.commit()

        flags = {f["taxon"]: f for f in recon_reliability_flags(db, gap_threshold=0.4)}
        # with the two hidden zeros excluded, visible recon = [1.0, 1.0] → mean 1.0, no gap
        assert flags["RR tom"]["recon_mean"] == 1.0
        assert flags["RR tom"]["n_recon"] == 2
        assert flags["RR tom"]["flag"] is False
        _clean(db)


def test_recon_reliability_flags_bad_reference_not_hard_taxon():
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="rr-cat", name="C")
        db.add(cat)
        db.flush()
        # gourd-like: recon collapses, text fine -> flagged (reference/input suspect)
        _taxon(db, cat, "gourd", recon_scores=[0.0, 0.0, 1.0], text_scores=[1.0, 1.0])
        # puffball-like: both fine -> not flagged
        _taxon(db, cat, "puff", recon_scores=[1.0, 1.0], text_scores=[1.0, 1.0])
        # only recon scored -> omitted (not comparable)
        _taxon(db, cat, "lonely", recon_scores=[0.0], text_scores=[])
        db.commit()

        flags = {f["taxon"]: f for f in recon_reliability_flags(db, gap_threshold=0.4)}
        assert flags["RR gourd"]["flag"] is True
        assert abs(flags["RR gourd"]["recon_mean"] - (1.0 / 3)) < 1e-9
        assert flags["RR gourd"]["text_mean"] == 1.0
        assert flags["RR puff"]["flag"] is False  # no gap
        assert "RR lonely" not in flags  # only one paradigm → omitted
        # sorted by gap descending: gourd (0.67) before puff (0.0)
        ordered = [f["taxon"] for f in recon_reliability_flags(db)]
        assert ordered.index("RR gourd") < ordered.index("RR puff")
        _clean(db)
