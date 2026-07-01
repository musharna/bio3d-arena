from __future__ import annotations

import trimesh

from app import commission
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task, Category


def setup_module(_m):
    init_db()


def _task(db, cat_slug="t-cat"):
    cat = Category(slug=cat_slug, name="c")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title="tomato", prompt="a tomato")
    db.add(t)
    db.commit()
    return t.id


def test_ingest_ok_creates_output_and_attempt(tmp_path):
    with SessionLocal() as db:
        tid = _task(db)
        glb = tmp_path / "gen.glb"
        trimesh.creation.box().export(str(glb))
        run = {
            "status": "ok",
            "stderr": "",
            "duration_ms": 1234,
            "glb_path": str(glb),
            "mesh_stats": {"vertices": 8, "faces": 12},
        }
        att = commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="anthropic/claude-opus-4.8",
            run=run,
            script="import bpy",
            asset_dir=tmp_path / "assets",
        )
        assert att.status == "ok" and att.output_id is not None
        out = db.get(ModelOutput, att.output_id)
        assert out.source == "commissioned" and out.asset_format == "glb"
        assert (tmp_path / "assets" / out.asset_path).exists()
        assert db.query(Generator).filter_by(id=att.generator_id).one().kind == "model"


def test_ingest_failure_writes_attempt_without_output(tmp_path):
    with SessionLocal() as db:
        tid = _task(db, cat_slug="t-cat-2")
        run = {
            "status": "error",
            "stderr": "boom",
            "duration_ms": 50,
            "glb_path": None,
            "mesh_stats": {},
        }
        att = commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="openai/gpt-x",
            run=run,
            script="bad",
            asset_dir=tmp_path / "assets",
        )
        assert att.status == "error" and att.output_id is None
        assert att.error == "boom" and att.script == "bad"
