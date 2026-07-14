# tests/test_commission_protocol.py
"""What /procedural measures: the model's MODELLING, not its memory of our plumbing.

Three defects, found by reproducing the sweep's own failures under the sweep's own conditions:

  1. WE SCORED OUR OWN BOILERPLATE. The prompt made the model reproduce the GLB export call from
     memory, and 14% of all failures died there -- on invented `export_scene.gltf` kwargs
     (use_selected, export_colors, export_y_up), with the organism already built. The harness now
     owns the export. A model is scored on the mesh it builds, never on whether it remembers our
     plumbing.

  2. A CRASHED SCRIPT WAS CALLED AN EMPTY MESH. Blender exits 0 even when a --python script raises
     (verified: an uncaught exception prints a traceback and returns 0). run_bpy only checked the
     exit code, so every crashed script fell through to the mesh check, found no GLB, and was
     recorded as `invalid_mesh`. That is why 98% of "invalid_mesh" rows carry a Python traceback.
     The runner now catches the model's exception itself and exits 3, so `script_error` (the model
     wrote broken code) is distinct from `invalid_mesh` (the script ran and built nothing).

  3. ONE SAMPLE PER CELL IS A COIN FLIP. Re-running grok-4.20 on taxa the sweep scored FAIL came
     back ['ok','invalid_mesh'] and ['invalid_mesh','ok'] -- ~50% on cells published as a flat 0.
     Its recorded 2/17 was noise, not capability. The harness now runs a REPAIR loop (hand the
     traceback back, up to 2 retries) and records BOTH outcomes on the one row: `status_oneshot`
     (unaided, what pass@1 has always claimed to be) and `status` (after repair). Reporting both is
     the point -- neither number alone is the honest one.
"""

from __future__ import annotations

import shutil

import pytest

from app import commission


# --- 1. the harness owns the export -------------------------------------------------------------


def test_prompt_does_not_ask_the_model_to_export():
    """Every character of the task should be about the ORGANISM. The moment the prompt mentions
    OUT_GLB, a model that builds a perfect mushroom can still score zero by mis-remembering a
    kwarg on our export call -- which is exactly what happened to 14% of the failures."""
    prompt = commission.build_prompt("Amanita muscaria", "fly agaric")

    assert "OUT_GLB" not in prompt
    assert "export" not in prompt.lower()


@pytest.mark.skipif(shutil.which("blender") is None, reason="blender not installed")
def test_harness_exports_a_script_that_never_exports(tmp_path):
    """The load-bearing one: the model builds geometry and simply stops. The harness must carry it
    to a GLB by itself."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=2)\n"
    out = tmp_path / "out.glb"

    res = commission.run_bpy(script, out_glb=out, timeout_s=120)

    assert res["status"] == "ok"
    assert commission.is_valid_mesh(out)[0] is True


# --- 2. a crash is a crash, not an empty mesh ----------------------------------------------------


@pytest.mark.skipif(shutil.which("blender") is None, reason="blender not installed")
def test_model_code_that_raises_is_script_error_with_its_traceback(tmp_path):
    """`mesh.calc_normals()` was removed in Blender 4.0 and is a real failure from the sweep. It
    must read as the model writing broken code -- and the traceback must survive, because the
    repair loop feeds it straight back to the model."""
    script = (
        "import bpy\n"
        "bpy.ops.mesh.primitive_cube_add(size=2)\n"
        "bpy.context.object.data.calc_normals()\n"
    )

    res = commission.run_bpy(script, out_glb=tmp_path / "out.glb", timeout_s=120)

    assert res["status"] == "script_error"
    assert "calc_normals" in res["stderr"]
    assert "AttributeError" in res["stderr"]


@pytest.mark.skipif(shutil.which("blender") is None, reason="blender not installed")
def test_a_script_that_runs_but_builds_nothing_is_invalid_mesh(tmp_path):
    """The other side of the split: no exception, no geometry. `invalid_mesh` now means only this."""
    script = "import bpy\nbpy.ops.object.select_all(action='SELECT')\nbpy.ops.object.delete()\n"

    res = commission.run_bpy(script, out_glb=tmp_path / "out.glb", timeout_s=120)

    assert res["status"] == "invalid_mesh"


def test_our_export_breaking_is_a_harness_error_never_a_model_failure():
    """If OUR export call is what died, the model built its organism and we lost it. That is the
    2026-07 pattern for the third time (sandbox, then billing, now export): our infrastructure
    failing must never be recorded as the model's result. Fail loud."""
    with pytest.raises(commission.HarnessError, match="export"):
        commission.classify_exit(commission.EXIT_HARNESS_EXPORT_ERROR, stderr="boom")

    assert commission.classify_exit(commission.EXIT_MODEL_ERROR, stderr="x") == "script_error"
    assert commission.classify_exit(0, stderr="") is None  # 0 -> defer to the mesh check


# --- 3. the repair loop, and recording both outcomes ---------------------------------------------


def _run_fn_from(statuses):
    """A run_fn that yields the given statuses in order, one per call."""
    seq = iter(statuses)

    def run_fn(script, out_glb):
        status = next(seq)
        return {
            "status": status,
            "stderr": "Traceback: boom" if status != "ok" else "",
            "duration_ms": 1,
            "glb_path": str(out_glb) if status == "ok" else None,
            "mesh_stats": {"vertices": 8, "faces": 12} if status == "ok" else {},
        }

    return run_fn


def test_a_script_that_works_first_try_costs_exactly_one_call(tmp_path):
    calls = []

    res = commission.run_with_repair(
        complete_fn=lambda m, p: (calls.append(p), "import bpy")[1],
        run_fn=_run_fn_from(["ok"]),
        model_id="m",
        prompt="build a fly agaric",
        out_glb=tmp_path / "out.glb",
        max_repairs=2,
    )

    assert len(calls) == 1, "a passing script must not be sent for repair"
    assert res["rounds"] == 1
    assert res["status_oneshot"] == "ok" and res["run"]["status"] == "ok"


def test_a_failing_script_is_repaired_and_both_outcomes_are_kept(tmp_path):
    """The heart of it. The unaided attempt failed -- that is a real, publishable fact and pass@1
    keeps it. It then succeeded with the traceback in hand, which is how anyone actually uses these
    models. BOTH go on the row; picking one and hiding the other is how we got 2/17."""
    prompts = []

    res = commission.run_with_repair(
        complete_fn=lambda m, p: (prompts.append(p), "import bpy")[1],
        run_fn=_run_fn_from(["script_error", "ok"]),
        model_id="m",
        prompt="build a fly agaric",
        out_glb=tmp_path / "out.glb",
        max_repairs=2,
    )

    assert res["status_oneshot"] == "script_error", "the unaided result must survive the repair"
    assert res["run"]["status"] == "ok"
    assert res["rounds"] == 2
    assert "Traceback: boom" in prompts[1], "the repair prompt must carry the actual error"


def test_repair_gives_up_after_max_repairs(tmp_path):
    res = commission.run_with_repair(
        complete_fn=lambda m, p: "import bpy",
        run_fn=_run_fn_from(["script_error", "script_error", "invalid_mesh"]),
        model_id="m",
        prompt="p",
        out_glb=tmp_path / "out.glb",
        max_repairs=2,
    )

    assert res["rounds"] == 3  # 1 unaided + 2 repairs
    assert res["status_oneshot"] == "script_error"
    assert res["run"]["status"] == "invalid_mesh"


def test_repair_prompt_carries_the_failing_script_and_the_error():
    p = commission.repair_prompt("build a fly agaric", "import bpy\nbroken()", "NameError: broken")

    assert "import bpy\nbroken()" in p
    assert "NameError: broken" in p
    assert "fly agaric" in p, "the repair must not lose the original task"


# --- pinning the sampler -------------------------------------------------------------------------


def test_temperature_is_pinned_on_the_wire():
    """The sweep sent no temperature at all, so every cell ran at each provider's default and the
    board was partly measuring sampling noise. A benchmark picks its own sampler."""
    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "x"}}]}

    def post(url, **kw):
        sent.update(kw["json"])
        return _Resp()

    commission.openrouter_complete(post, "m", "p", api_key="k", temperature=0.2)

    assert sent["temperature"] == 0.2


# --- what the board reports -----------------------------------------------------------------


def test_scorecard_reports_both_numbers_and_ignores_the_legacy_rows(tmp_path):
    """The legacy rows measured a harness that scored its own export boilerplate and filed crashes
    as empty meshes. They are kept as evidence, but a board that mixed them with new rows would be
    averaging two different experiments."""
    from app import service
    from app.database import SessionLocal, init_db
    from app.models import Category, CommissionAttempt, Generator, Task

    init_db()
    with SessionLocal() as db:
        cat = Category(slug="sc-test", name="Scorecard test")
        db.add(cat)
        db.flush()
        # One row per (model, task) is the schema's rule, so four outcomes need four tasks.
        tasks = [Task(category_id=cat.id, title=f"t{i}", prompt="p") for i in range(4)]
        db.add_all(tasks)
        db.flush()
        gen = Generator(slug="sc-model", name="sc-model", kind="model", paradigm="procedural_llm")
        db.add(gen)
        db.flush()

        def att(task, status, oneshot, rounds, protocol):
            return CommissionAttempt(
                task_id=task.id,
                model_id="sc-model",
                generator_id=gen.id,
                status=status,
                status_oneshot=oneshot,
                rounds=rounds,
                protocol=protocol,
            )

        db.add_all(
            [
                att(tasks[0], "ok", "ok", 1, "repair"),  # passed unaided
                att(tasks[1], "ok", "script_error", 2, "repair"),  # passed only after repair
                att(tasks[2], "invalid_mesh", "invalid_mesh", 3, "repair"),  # never got there
                att(tasks[3], "error", "", 0, "legacy"),  # must not count at all
            ]
        )
        db.commit()

        row = next(r for r in service.procedural_scorecard(db) if r["model"] == "sc-model")

        assert row["attempts"] == 3, "a legacy row leaked into the board"
        assert row["n_oneshot"] == 1 and row["pass_at_1"] == pytest.approx(1 / 3)
        assert row["valid"] == 2 and row["pass_repair"] == pytest.approx(2 / 3)
        assert row["n_repaired"] == 1, "the model that needed its traceback back must be visible"

        db.query(CommissionAttempt).filter_by(model_id="sc-model").delete()
        for t in tasks:
            db.query(Task).filter_by(id=t.id).delete()
        db.query(Generator).filter_by(id=gen.id).delete()
        db.query(Category).filter_by(id=cat.id).delete()
        db.commit()


def test_a_legacy_row_never_blocks_the_rerun():
    """existing_pairs is protocol-scoped. If it were not, every pair the broken harness already
    touched would be skipped forever and the re-run would measure nothing."""
    from app.database import SessionLocal, init_db
    from app.models import Category, CommissionAttempt, Task

    init_db()
    with SessionLocal() as db:
        cat = Category(slug="ep-test", name="Existing pairs test")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t", prompt="p")
        db.add(task)
        db.flush()
        db.add(
            CommissionAttempt(
                task_id=task.id, model_id="ep-model", status="error", protocol="legacy"
            )
        )
        db.commit()

        assert ("ep-model", task.id) not in commission.existing_pairs(db, "repair")
        assert ("ep-model", task.id) in commission.existing_pairs(db, "legacy")

        db.query(CommissionAttempt).filter_by(model_id="ep-model").delete()
        db.query(Task).filter_by(id=task.id).delete()
        db.query(Category).filter_by(id=cat.id).delete()
        db.commit()
