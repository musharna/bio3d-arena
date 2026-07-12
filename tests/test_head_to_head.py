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


def _mk(db, slug, paradigm="image_recon"):
    g = Generator(slug=slug, name=slug, paradigm=paradigm)
    db.add(g)
    db.flush()
    return g


def test_head_to_head_counts_wins_losses_and_pct():
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        cat = Category(slug="h2h-cat", name="H")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="h2h-task", prompt="p")
        db.add(task)
        db.flush()
        a, b = _mk(db, "h2h-a"), _mk(db, "h2h-b")
        oa = ModelOutput(task_id=task.id, generator_id=a.id, asset_path="a.glb", asset_format="glb")
        ob = ModelOutput(task_id=task.id, generator_id=b.id, asset_path="b.glb", asset_format="glb")
        db.add_all([oa, ob])
        db.flush()
        # 3 comparisons A vs B: A wins twice, B wins once.
        for winner in ("a", "a", "b"):
            c = Comparison(
                task_id=task.id,
                criterion_id=crit.id,
                output_a_id=oa.id,
                output_b_id=ob.id,
                session_id="h2h-sess",
                is_gold=False,
            )
            db.add(c)
            db.flush()
            db.add(Vote(comparison_id=c.id, winner=winner, session_id="h2h-sess"))
        db.commit()

        rec = service.head_to_head_record(db, a.id, "overall")
        assert len(rec) == 1
        row = rec[0]
        assert row["opponent_id"] == b.id
        assert row["wins"] == 2 and row["losses"] == 1 and row["games"] == 3
        assert abs(row["win_pct"] - 2 / 3) < 1e-6


def test_head_to_head_empty_when_no_games():
    with SessionLocal() as db:
        g = _mk(db, "h2h-lonely")
        db.commit()
        assert service.head_to_head_record(db, g.id, "overall") == []
