from app.database import SessionLocal, init_db
from app.models import Category, Critique, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_critique_round_trips():
    db = SessionLocal()
    try:
        cat = Category(slug="c-crit", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-crit", prompt="p")
        gen = Generator(slug="g-crit", name="G")
        db.add_all([task, gen])
        db.flush()
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.flush()
        c = Critique(output_id=out.id, render_path="renders/x.png", critic_note="flat petals")
        db.add(c)
        db.flush()
        got = db.query(Critique).filter_by(output_id=out.id).one()
        assert got.render_path == "renders/x.png"
        assert got.critic_note == "flat petals"
        assert got.status == "ok"
        assert got.dists is None
    finally:
        db.close()
