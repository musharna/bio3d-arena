# tests/test_dgen_ingest.py
import tempfile
from pathlib import Path

import pytest

from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Generator,
    ModelOutput,
    Task,
    TraitRubric,
    TraitVerdict,
    Completeness,
    DGenRun,
    DGenIteration,
)
from app.dgen import ingest_best


def setup_module(_m):
    init_db()


def _seed(db):
    cat = Category(slug="soy-dgen-test", name="Glycine max")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt="a soybean plant")
    db.add(task)
    db.flush()
    db.add(TraitRubric(task_id=task.id, taxon="Glycine max", traits_json="[]"))
    run = DGenRun(model_id="gemini-x")
    db.add(run)
    db.flush()
    it = DGenIteration(run_id=run.id, taxon="Glycine max", round=2, fidelity=0.8, status="ok")
    db.add(it)
    db.flush()
    return task.id, it


def test_ingest_best_creates_output_verdicts_completeness_and_marks_best():
    with SessionLocal() as db:
        task_id, it = _seed(db)
        best_score = {
            "trait_results": [
                {
                    "trait_key": "leaf_form",
                    "trait_class": "organ_shape",
                    "verdict": "present_correct",
                    "rationale": "ok",
                },
                {
                    "trait_key": "has_pod",
                    "trait_class": "presence",
                    "verdict": "absent",
                    "rationale": "none",
                },
            ],
            "completeness_category": "complete",
            "completeness_score": 1.0,
            "completeness_organs_present": [{"key": "vegetative_axis", "status": "present"}],
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.glb"
            src.write_bytes(b"GLB")
            asset_dir = Path(td) / "assets"
            oid = ingest_best(
                db,
                model_id="gemini-x",
                task_id=task_id,
                glb_src=src,
                best_score=best_score,
                best_iter=it,
                asset_dir=str(asset_dir),
            )
            db.commit()

            out = db.get(ModelOutput, oid)
            assert out.source == "commissioned"
            gen = db.get(Generator, out.generator_id)
            assert gen.slug.startswith("openrouter-") and gen.slug.endswith("-dgen")
            assert gen.paradigm == "procedural_llm"
            assert db.query(TraitVerdict).filter_by(output_id=oid).count() == 2
            comp = db.query(Completeness).filter_by(output_id=oid).one()
            assert comp.category == "complete"
            checklist = comp.checklist_json
            if isinstance(checklist, str):
                import json as _json

                checklist = _json.loads(checklist)
            assert checklist["organs_present"] == best_score["completeness_organs_present"]
            assert it.output_id == oid and it.is_best is True
            assert (asset_dir / "dgen" / f"{gen.slug}_{task_id}.glb").exists()


def test_ingest_best_raises_without_trait_rubric():
    with SessionLocal() as db:
        cat = Category(slug="soy-dgen-norubric-test", name="Glycine max")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t", prompt="a soybean plant")
        db.add(task)
        db.flush()
        # Deliberately no TraitRubric for this task.
        run = DGenRun(model_id="gemini-x")
        db.add(run)
        db.flush()
        it = DGenIteration(run_id=run.id, taxon="Glycine max", round=1, fidelity=0.5, status="ok")
        db.add(it)
        db.flush()

        best_score = {
            "trait_results": [],
            "completeness_category": "complete",
            "completeness_score": 1.0,
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.glb"
            src.write_bytes(b"GLB")
            asset_dir = Path(td) / "assets"
            with pytest.raises(ValueError):
                ingest_best(
                    db,
                    model_id="gemini-x",
                    task_id=task.id,
                    glb_src=src,
                    best_score=best_score,
                    best_iter=it,
                    asset_dir=str(asset_dir),
                )
