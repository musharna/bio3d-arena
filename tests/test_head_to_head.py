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


def test_head_to_head_tie_counts_as_half_win():
    """1 decisive win + 1 tie vs one opponent: ties use the 0.5 convention, and
    `games` counts real comparisons (not the BT split-tie double-count)."""
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        cat = Category(slug="h2h-tie-cat", name="H-tie")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="h2h-tie-task", prompt="p")
        db.add(task)
        db.flush()
        a, b = _mk(db, "h2h-tie-a"), _mk(db, "h2h-tie-b")
        oa = ModelOutput(task_id=task.id, generator_id=a.id, asset_path="a.glb", asset_format="glb")
        ob = ModelOutput(task_id=task.id, generator_id=b.id, asset_path="b.glb", asset_format="glb")
        db.add_all([oa, ob])
        db.flush()
        for winner in ("a", "tie"):
            c = Comparison(
                task_id=task.id,
                criterion_id=crit.id,
                output_a_id=oa.id,
                output_b_id=ob.id,
                session_id="h2h-tie-sess",
                is_gold=False,
            )
            db.add(c)
            db.flush()
            db.add(Vote(comparison_id=c.id, winner=winner, session_id="h2h-tie-sess"))
        db.commit()

        rec = service.head_to_head_record(db, a.id, "overall")
        assert len(rec) == 1
        row = rec[0]
        assert row["opponent_id"] == b.id
        assert row["wins"] == 1 and row["losses"] == 0 and row["ties"] == 1
        assert row["games"] == 2
        assert abs(row["win_pct"] - 0.75) < 1e-6


def test_head_to_head_sort_order_by_games_desc():
    """Three opponents with different games counts must come back games-desc — the
    existing tests only ever produce a single opponent, so the sort was unexercised."""
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        cat = Category(slug="h2h-sort-cat", name="H-sort")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="h2h-sort-task", prompt="p")
        db.add(task)
        db.flush()
        a = _mk(db, "h2h-sort-a")
        c1, c2, c3 = (
            _mk(db, "h2h-sort-c1"),
            _mk(db, "h2h-sort-c2"),
            _mk(db, "h2h-sort-c3"),
        )
        oa = ModelOutput(task_id=task.id, generator_id=a.id, asset_path="a.glb", asset_format="glb")
        o1 = ModelOutput(
            task_id=task.id, generator_id=c1.id, asset_path="c1.glb", asset_format="glb"
        )
        o2 = ModelOutput(
            task_id=task.id, generator_id=c2.id, asset_path="c2.glb", asset_format="glb"
        )
        o3 = ModelOutput(
            task_id=task.id, generator_id=c3.id, asset_path="c3.glb", asset_format="glb"
        )
        db.add_all([oa, o1, o2, o3])
        db.flush()

        def _votes(out_b, n):
            for _ in range(n):
                c = Comparison(
                    task_id=task.id,
                    criterion_id=crit.id,
                    output_a_id=oa.id,
                    output_b_id=out_b.id,
                    session_id="h2h-sort-sess",
                    is_gold=False,
                )
                db.add(c)
                db.flush()
                db.add(Vote(comparison_id=c.id, winner="a", session_id="h2h-sort-sess"))

        _votes(o1, 1)
        _votes(o2, 2)
        _votes(o3, 3)
        db.commit()

        rec = service.head_to_head_record(db, a.id, "overall")
        assert [r["opponent_id"] for r in rec] == [c3.id, c2.id, c1.id]
        assert [r["games"] for r in rec] == [3, 2, 1]


def test_head_to_head_excludes_self_pair():
    """A comparison whose two outputs belong to the same generator must not make that
    generator appear as its own opponent (matchmaking doesn't guarantee distinct
    generators; 69 such self-pairs exist in the live DB, 10 with votes)."""
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        cat = Category(slug="h2h-self-cat", name="H-self")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="h2h-self-task", prompt="p")
        db.add(task)
        db.flush()
        g = _mk(db, "h2h-self-g")
        o1 = ModelOutput(
            task_id=task.id, generator_id=g.id, asset_path="g1.glb", asset_format="glb"
        )
        o2 = ModelOutput(
            task_id=task.id, generator_id=g.id, asset_path="g2.glb", asset_format="glb"
        )
        db.add_all([o1, o2])
        db.flush()
        c = Comparison(
            task_id=task.id,
            criterion_id=crit.id,
            output_a_id=o1.id,
            output_b_id=o2.id,
            session_id="h2h-self-sess",
            is_gold=False,
        )
        db.add(c)
        db.flush()
        db.add(Vote(comparison_id=c.id, winner="a", session_id="h2h-self-sess"))
        db.commit()

        rec = service.head_to_head_record(db, g.id, "overall")
        assert rec == []
