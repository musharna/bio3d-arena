import json

import trimesh
from sqlalchemy import select

from app import agentic
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task

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


def _ok_run(vertices):
    """A run_fn that writes a real box GLB and reports `vertices`."""

    def run_fn(script, out_glb):
        from pathlib import Path

        Path(out_glb).write_bytes(trimesh.creation.box().export(file_type="glb"))
        return {
            "status": "ok",
            "glb_path": str(out_glb),
            "mesh_stats": {"vertices": vertices, "faces": 12, "meshes": 1},
            "duration_ms": 1,
        }

    return run_fn


def _bad_run(script, out_glb):
    return {"status": "invalid_mesh", "glb_path": None, "mesh_stats": {}, "duration_ms": 1}


def test_agentic_adopts_valid_revision(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        calls = {"run": 0}

        def run_fn(script, out_glb):
            calls["run"] += 1
            return _ok_run(8 if calls["run"] == 1 else 99)(script, out_glb)

        rep = agentic.agentic_generate(
            db,
            model_id="openai/gpt-5.1",
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "```python\npass\n```",
            vision_fn=lambda prompt, png: "```python\npass\n```",
            run_fn=run_fn,
            render_fn=lambda glb: b"\x89PNGfake",
            asset_dir=str(tmp_path),
            n_iters=2,
        )
        assert rep["status"] == "ok" and rep["n_iterations"] == 2
        out = db.execute(select(ModelOutput).where(ModelOutput.id == rep["output_id"])).scalar_one()
        assert out.source == "agentic:openai/gpt-5.1"
        meta = json.loads(out.meta_json)
        assert meta["modality"] == "agentic" and meta["n_iterations"] == 2
        assert meta["iter_vertices"] == [8, 99]
        assert out.generator.paradigm != "procedural_llm"  # distinct generator
    finally:
        db.close()


def test_agentic_keeps_iter0_when_revision_invalid(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        calls = {"run": 0}

        def run_fn(script, out_glb):
            calls["run"] += 1
            return _ok_run(8)(script, out_glb) if calls["run"] == 1 else _bad_run(script, out_glb)

        rep = agentic.agentic_generate(
            db,
            model_id="x/m",
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "s",
            vision_fn=lambda prompt, png: "s",
            run_fn=run_fn,
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=2,
        )
        assert rep["status"] == "ok" and rep["n_iterations"] == 1  # kept iter-0, no regression
    finally:
        db.close()


def test_agentic_no_output_when_iter0_invalid(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        rep = agentic.agentic_generate(
            db,
            model_id="x/m2",
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "s",
            vision_fn=lambda prompt, png: "s",
            run_fn=_bad_run,
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=2,
        )
        assert rep["status"] == "invalid_mesh"
        gen = agentic.get_or_create_agentic_generator(db, "x/m2")
        assert db.query(ModelOutput).filter_by(generator_id=gen.id).count() == 0
    finally:
        db.close()


def test_agentic_idempotent(tmp_path):
    db = SessionLocal()
    try:
        t = _task(db)
        kw = dict(
            task_id=t.id,
            species="Zea mays",
            common="maize",
            complete_fn=lambda prompt: "s",
            vision_fn=lambda prompt, png: "s",
            run_fn=_ok_run(8),
            render_fn=lambda glb: b"png",
            asset_dir=str(tmp_path),
            n_iters=1,
        )
        r1 = agentic.agentic_generate(db, model_id="x/m3", **kw)
        r2 = agentic.agentic_generate(db, model_id="x/m3", **kw)
        assert r1["status"] == "ok" and r2["status"] == "skipped_exists"
        gen = agentic.get_or_create_agentic_generator(db, "x/m3")
        assert db.query(ModelOutput).filter_by(generator_id=gen.id).count() == 1
    finally:
        db.close()
