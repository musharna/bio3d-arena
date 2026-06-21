from app.database import SessionLocal, init_db
from app.models import Category, Critique, Generator, ModelOutput, Task
from scripts.render_spotlight import render_outputs


def setup_module(_m):
    init_db()


def test_render_outputs_upserts_critique(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    db = SessionLocal()
    try:
        cat = Category(slug="c-r", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-r", prompt="p")
        gen = Generator(slug="g-r", name="G")
        db.add_all([task, gen])
        db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        (tmp_path / "seed" / "x.glb").write_bytes(b"glTF-stub")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.commit()
        res = render_outputs(db, [out.id], capture=lambda p: b"\x89PNG-fake-bytes")
        assert res["rendered"] == 1
        crit = db.query(Critique).filter_by(output_id=out.id).one()
        assert crit.render_path == f"renders/{out.id}.png"
        assert (tmp_path / crit.render_path).read_bytes() == b"\x89PNG-fake-bytes"
    finally:
        db.close()
