from __future__ import annotations

import trimesh

from app import commission
from app.database import SessionLocal, init_db
from app.models import CommissionAttempt, Generator, ModelOutput, Task, Category, TraitRubric


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


def _rubric_task(db, taxon):
    cat = Category(slug=f"c-{taxon}", name="c")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=taxon, prompt=f"a {taxon}")
    db.add(t)
    db.flush()
    db.add(TraitRubric(taxon=taxon, task_id=t.id, traits_json="[]"))
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


def test_a_reruns_mesh_never_displaces_the_entrant_people_already_voted_on(tmp_path):
    """A re-run under a new protocol is a new MEASUREMENT of a cell, not a new entrant in the arena.

    The arena's live invariant is one output per (task, generator) — 0 duplicate groups in the real
    DB — and 18 votes already point at commissioned meshes. Widening the attempt's identity by
    protocol removed the UNIQUE constraint that was implicitly enforcing that invariant, so without
    this guard the re-run would (a) shutil.copyfile over the very GLB those votes were cast on and
    (b) enter every model twice on every task it had already covered, which is the same-generator
    BT pollution we fixed once already.

    So: the attempt is recorded in full (that is the deliverable — pass@1 / pass@repair), and the
    cell's existing entrant is left exactly as it stands, mesh and votes intact."""
    with SessionLocal() as db:
        tid = _task(db, cat_slug="t-cat-rerun")
        assets = tmp_path / "assets"

        first = tmp_path / "first.glb"
        trimesh.creation.box().export(str(first))
        att1 = commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="x-ai/grok-4.20",
            run={
                "status": "ok",
                "stderr": "",
                "duration_ms": 10,
                "glb_path": str(first),
                "mesh_stats": {"vertices": 8},
            },
            script="import bpy  # legacy",
            asset_dir=assets,
            protocol="legacy",
        )
        out1 = db.get(ModelOutput, att1.output_id)
        entrant_path = assets / out1.asset_path
        mesh_before = entrant_path.read_bytes()

        # the same cell, re-measured under the fixed harness, producing a DIFFERENT mesh
        second = tmp_path / "second.glb"
        trimesh.creation.icosphere().export(str(second))
        att2 = commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="x-ai/grok-4.20",
            run={
                "status": "ok",
                "stderr": "",
                "duration_ms": 20,
                "glb_path": str(second),
                "mesh_stats": {"vertices": 42},
            },
            script="import bpy  # repair",
            asset_dir=assets,
            protocol="repair",
            status_oneshot="ok",
            rounds=1,
        )

        # the measurement is recorded in full
        assert att2.status == "ok" and att2.protocol == "repair"
        assert att2.script == "import bpy  # repair"
        assert '"vertices": 42' in att2.mesh_stats_json

        # ...and the arena is untouched: one entrant, same mesh bytes, same output row
        outs = db.query(ModelOutput).filter_by(task_id=tid, generator_id=att2.generator_id).all()
        assert len(outs) == 1, "the re-run entered the same model twice on one task"
        assert outs[0].id == out1.id
        assert entrant_path.read_bytes() == mesh_before, "the re-run overwrote a voted-on mesh"
        assert att2.output_id is None  # status='ok' is what says the mesh was valid


def test_a_rerun_DOES_add_an_entrant_when_the_cell_has_none(tmp_path):
    """The other half: a cell the broken harness recorded as a flat failure has no entrant, so a
    model that now succeeds must actually enter the arena. This is the point of the re-run —
    previously-'failing' models get their mesh in the pool."""
    with SessionLocal() as db:
        tid = _task(db, cat_slug="t-cat-rerun-2")
        assets = tmp_path / "assets2"

        commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="x-ai/grok-4.20",
            run={"status": "invalid_mesh", "stderr": "boom", "duration_ms": 9, "glb_path": None},
            script="import bpy",
            asset_dir=assets,
            protocol="legacy",
        )

        glb = tmp_path / "now_works.glb"
        trimesh.creation.box().export(str(glb))
        att = commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="x-ai/grok-4.20",
            run={
                "status": "ok",
                "stderr": "",
                "duration_ms": 30,
                "glb_path": str(glb),
                "mesh_stats": {"vertices": 8},
            },
            script="import bpy",
            asset_dir=assets,
            protocol="repair",
            status_oneshot="ok",
            rounds=1,
        )

        assert att.output_id is not None
        out = db.get(ModelOutput, att.output_id)
        assert (assets / out.asset_path).exists()


def test_run_batch_persists_and_resumes(tmp_path):
    import trimesh

    with SessionLocal() as db:
        tid = _rubric_task(db, "Solanum lycopersicum")

        def complete_fn(model_id, prompt):
            return "```python\nimport bpy\n```"

        def run_fn(script, out_glb):
            trimesh.creation.box().export(str(out_glb))
            return {
                "status": "ok",
                "stderr": "",
                "duration_ms": 5,
                "glb_path": str(out_glb),
                "mesh_stats": {"vertices": 8, "faces": 12},
            }

        roster = ["anthropic/claude-opus-4.8"]
        tt = [("Solanum lycopersicum", tid)]
        res = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=roster,
            taxon_tasks=tt,
            asset_dir=tmp_path / "assets",
        )
        assert res["ok"] == 1
        att = db.query(CommissionAttempt).filter_by(model_id=roster[0], task_id=tid).one()
        assert att.status == "ok"

        # resume: same pair is skipped, no second attempt
        res2 = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=roster,
            taxon_tasks=tt,
            asset_dir=tmp_path / "assets",
        )
        assert res2["skipped"] == 1 and res2["ok"] == 0
        assert db.query(CommissionAttempt).filter_by(model_id=roster[0], task_id=tid).count() == 1


def test_run_batch_emits_a_progress_heartbeat_for_each_attempted_cell(tmp_path):
    """A long batch harness must emit stdout as it works, not only at start and finish.

    The first jobd run of this sweep was SIGTERM'd at exactly its 3600s idle timeout after doing
    11 real cells in that hour: run_batch printed the plan line and then nothing until the final
    summary, so the broker's stdout-idle watchdog judged a productive job dead. The fix is a
    per-cell progress callback the runner uses to print (and flush) a heartbeat — it both keeps the
    idle timer alive and makes the logs actually track progress. Skipped (already-attempted) cells
    are instant and don't heartbeat; only real work does."""
    with SessionLocal() as db:
        tid1 = _rubric_task(db, "Zea mays")
        tid2 = _rubric_task(db, "Solanum lycopersicum")

        def complete_fn(model_id, prompt):
            return "```python\nimport bpy\n```"

        def run_fn(script, out_glb):
            trimesh.creation.box().export(str(out_glb))
            return {
                "status": "ok",
                "stderr": "",
                "duration_ms": 5,
                "glb_path": str(out_glb),
                "mesh_stats": {"vertices": 8},
            }

        events = []
        commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=["m1"],
            taxon_tasks=[("Zea mays", tid1), ("Solanum lycopersicum", tid2)],
            asset_dir=tmp_path / "assets",
            on_progress=lambda done, total, model_id, task_id, status: events.append(
                (done, total, model_id, task_id, status)
            ),
        )

        # one heartbeat per attempted cell, with a monotonic done/total and the outcome
        assert [(e[0], e[1]) for e in events] == [(1, 2), (2, 2)]
        assert all(e[2] == "m1" and e[4] == "ok" for e in events)
        assert {e[3] for e in events} == {tid1, tid2}


def test_run_batch_does_not_heartbeat_skipped_cells(tmp_path):
    """A resume iterates every pair to skip the already-done ones; those skips are instant set
    lookups and must not fire the heartbeat (it exists to mark real work, and firing on skips would
    flood the log at resume)."""
    with SessionLocal() as db:
        tid = _rubric_task(db, "Zea mays")

        def complete_fn(model_id, prompt):
            return "```python\nimport bpy\n```"

        def run_fn(script, out_glb):
            trimesh.creation.box().export(str(out_glb))
            return {
                "status": "ok",
                "stderr": "",
                "duration_ms": 5,
                "glb_path": str(out_glb),
                "mesh_stats": {"vertices": 8},
            }

        commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=["m1"],
            taxon_tasks=[("Zea mays", tid)],
            asset_dir=tmp_path / "assets",
        )
        events = []
        res2 = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=["m1"],
            taxon_tasks=[("Zea mays", tid)],
            asset_dir=tmp_path / "assets",
            on_progress=lambda *a: events.append(a),
        )
        assert res2["skipped"] == 1 and events == []


def test_dry_run_plan_counts_uncovered_pairs(tmp_path):
    from scripts import commission_arena

    with SessionLocal() as db:
        tid = _rubric_task(db, "Zea mays")
        plan = commission_arena.plan(db, roster=["m1", "m2"])
        assert plan["tasks"] == 1 and plan["roster"] == 2 and plan["calls_needed"] == 2


def test_get_or_create_generator_is_born_procedural_llm():
    """A commissioned generator's paradigm is KNOWN at creation — the commission harness only ever
    makes procedural_llm entrants (an LLM authoring Blender-Python). Leaving it blank makes the
    generator invisible to /procedural (which filters paradigm == 'procedural_llm') until a manual
    backfill, so the creator must assert the paradigm it definitionally knows."""
    with SessionLocal() as db:
        gen = commission.get_or_create_generator(db, "some-lab/new-model-xyz")
        assert gen.paradigm == "procedural_llm"


def test_get_or_create_generator_heals_a_blank_paradigm_on_an_existing_row():
    """A generator created before this fix (blank paradigm) is healed the next time the harness
    touches it — the creator tells the row the paradigm it knows. A row that already carries a
    DELIBERATE (non-blank) paradigm is left untouched."""
    from app.models import Generator

    with SessionLocal() as db:
        slug = commission.slug_for_model("some-lab/legacy-blank")
        db.add(Generator(slug=slug, name="some-lab/legacy-blank", kind="model", paradigm=""))
        db.flush()

        gen = commission.get_or_create_generator(db, "some-lab/legacy-blank")
        assert gen.paradigm == "procedural_llm"


def test_get_or_create_generator_respects_an_explicit_paradigm():
    """The paradigm is a parameter, defaulting to the common (procedural_llm) case, so a future
    caller creating a non-procedural entrant asserts its own paradigm rather than re-inventing the
    post-hoc assignment this fix removed."""
    with SessionLocal() as db:
        gen = commission.get_or_create_generator(db, "some-lab/agentic-model", paradigm="agentic")
        assert gen.paradigm == "agentic"
