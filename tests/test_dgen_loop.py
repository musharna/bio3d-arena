# tests/test_dgen_loop.py
from app.database import SessionLocal, init_db
from app.models import Category, Task, TraitRubric, DGenRun, DGenIteration
from app.dgen import _select_best, refine_loop


def setup_module(_m):
    init_db()


def _seed(db, taxon="Zea mays"):
    cat = Category(slug=f"{taxon[:4].lower()}-loop", name=taxon)
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt=f"a {taxon} plant")
    db.add(task)
    db.flush()
    db.add(TraitRubric(task_id=task.id, taxon=taxon, traits_json="[]"))
    run = DGenRun(model_id="m")
    db.add(run)
    db.flush()
    return task.id, run.id


def _score(fid, cat="complete"):
    return {
        "fidelity": fid,
        "n_correct": int((fid or 0) * 2),
        "n_assessable": 2,
        "trait_results": [
            {"trait_key": "a", "trait_class": "c", "verdict": "present_correct", "rationale": ""}
        ],
        "completeness_category": cat,
        "completeness_score": 1.0,
        "completeness_missing_organs": [],
    }


def _run_fn_ok(script, out_glb):
    from pathlib import Path

    Path(out_glb).write_bytes(b"GLB")
    return {
        "status": "ok",
        "glb_path": str(out_glb),
        "stderr": "",
        "duration_ms": 1,
        "mesh_stats": {},
    }


def test_select_best_prefers_fidelity_then_complete_then_earliest():
    rounds = [
        {
            "round": 0,
            "status": "ok",
            "comp_cat": "partial-organism",
            "score": _score(0.8, "partial-organism"),
        },
        {"round": 1, "status": "ok", "comp_cat": "complete", "score": _score(0.8, "complete")},
        {"round": 2, "status": "error", "comp_cat": "", "score": None},
    ]
    assert _select_best(rounds)["round"] == 1  # tie 0.8 -> complete wins
    assert _select_best([{"round": 0, "status": "error", "comp_cat": "", "score": None}]) is None


def test_loop_plateau_stops_and_promotes_best():
    with SessionLocal() as db:
        task_id, run_id = _seed(db, "Zea mays")
        db.commit()
        fids = iter(
            [0.4, 0.6, 0.6]
        )  # round2 (0.6) does NOT beat best 0.6 -> plateau stop after round2
        summary = refine_loop(
            db,
            run_id=run_id,
            taxon="Zea mays",
            task_id=task_id,
            prompt="p",
            common="maize",
            model_id="m",
            traits=[],
            complete_fn=lambda m, p: "import bpy",
            run_fn=_run_fn_ok,
            score_fn=lambda g: _score(next(fids)),
            asset_dir="/tmp/dgen_test_assets",
            max_rounds=3,
        )
        db.commit()
        iters = (
            db.query(DGenIteration)
            .filter_by(run_id=run_id, taxon="Zea mays")
            .order_by(DGenIteration.round)
            .all()
        )
        assert [i.round for i in iters] == [0, 1, 2]  # ran 3 rounds then plateau-stopped
        assert summary["best_round"] == 1  # first to reach 0.6
        best = [i for i in iters if i.is_best]
        assert len(best) == 1 and best[0].round == 1 and best[0].output_id is not None


def test_refine_loop_persists_round0_baseline_glb(tmp_path):
    from pathlib import Path

    from app.dgen import _taxon_slug

    with SessionLocal() as db:
        # NOTE: taxon="Vitis" (not "Rosa" as in the brief) — "Rosa" is already seeded by
        # test_loop_repairs_failed_round below in this same file, and _seed()'s category slug
        # (taxon[:4].lower()) would collide across the two tests: this suite's tests share one
        # engine/DB for the whole run (see conftest.py) and commit (no rollback isolation), so a
        # second _seed(db, "Rosa") hits UNIQUE constraint failed: category.slug.
        task_id, run_id = _seed(db, "Vitis")
        db.commit()
        asset_dir = tmp_path / "assets"
        refine_loop(
            db,
            run_id=run_id,
            taxon="Vitis",
            task_id=task_id,
            prompt="p",
            common="grape",
            model_id="m",
            traits=[],
            complete_fn=lambda m, p: "import bpy",
            run_fn=_run_fn_ok,
            score_fn=lambda g: _score(0.5),
            asset_dir=str(asset_dir),
            max_rounds=1,
        )
        db.commit()
        baseline = Path(asset_dir) / "dgen_baseline" / f"{run_id}_{_taxon_slug('Vitis')}.glb"
        assert baseline.exists()


def test_loop_repairs_failed_round():
    with SessionLocal() as db:
        task_id, run_id = _seed(db, "Rosa")
        db.commit()

        def run_fn(script, out_glb):
            return {
                "status": "error",
                "glb_path": None,
                "stderr": "boom",
                "duration_ms": 0,
                "mesh_stats": {},
            }

        calls = {"n": 0}

        def run_fn_then_ok(script, out_glb):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"status": "error", "glb_path": None, "stderr": "boom", "duration_ms": 0}
            return _run_fn_ok(script, out_glb)

        summary = refine_loop(
            db,
            run_id=run_id,
            taxon="Rosa",
            task_id=task_id,
            prompt="p",
            common="rose",
            model_id="m",
            traits=[],
            complete_fn=lambda m, p: "import bpy",
            run_fn=run_fn_then_ok,
            score_fn=lambda g: _score(0.7),
            asset_dir="/tmp/dgen_test_assets",
            max_rounds=3,
        )
        db.commit()
        iters = (
            db.query(DGenIteration)
            .filter_by(run_id=run_id, taxon="Rosa")
            .order_by(DGenIteration.round)
            .all()
        )
        assert iters[0].status == "error"  # round 0 failed -> did NOT plateau-stop
        assert summary["best_round"] is not None  # a later valid round was promoted
        assert any(i.is_best and i.output_id is not None for i in iters)
