import trimesh

from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.generate_agentic import run_agentic_batch

TITLE = "Zea mays — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="P")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=TITLE, prompt="p")
    db.add(t)
    db.commit()
    return t


def _run_fn(script, out_glb):
    from pathlib import Path

    Path(out_glb).write_bytes(trimesh.creation.box().export(file_type="glb"))
    return {
        "status": "ok",
        "glb_path": str(out_glb),
        "mesh_stats": {"vertices": 8},
        "duration_ms": 1,
    }


def test_run_agentic_batch_generates_and_is_idempotent(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        kw = dict(
            roster=["x/m"],
            taxon_tasks=[("Zea mays", t.id)],
            complete_fn=lambda model_id, prompt: "s",
            vision_fn=lambda model_id, prompt, png: "s",
            run_fn=_run_fn,
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=1,
        )
        r1 = run_agentic_batch(db, **kw)
        assert r1["ok"] == 1
        r2 = run_agentic_batch(db, **kw)  # idempotent second pass
        assert r2["skipped_exists"] == 1 and r2["ok"] == 0
        assert db.query(ModelOutput).filter(ModelOutput.source.like("agentic:%")).count() == 1
    finally:
        db.close()
