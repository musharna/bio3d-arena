from __future__ import annotations

import json
import uuid

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    CommissionAttempt,
    Generator,
    ModelOutput,
    ModelScope,
    Task,
    TraitVerdict,
)


def setup_module(_m):
    init_db()


def _mk_task(db) -> int:
    cat = Category(slug=f"proc-cat-{uuid.uuid4().hex}", name="C")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=f"proc-{uuid.uuid4().hex}", prompt="p")
    db.add(t)
    db.flush()
    return t.id


def test_scorecard_pass_at_1_fidelity_rank_and_exclusion():
    with SessionLocal() as db:
        tag = uuid.uuid4().hex
        # Generator A: procedural_llm, 2/2 ok attempts.
        gen_a = Generator(
            slug=f"pa-{tag}", name=f"model-a-{tag}", kind="model", paradigm="procedural_llm"
        )
        # Generator B: procedural_llm, 1/2 ok attempts.
        gen_b = Generator(
            slug=f"pb-{tag}", name=f"model-b-{tag}", kind="model", paradigm="procedural_llm"
        )
        # Generator C: different paradigm — must be excluded.
        gen_c = Generator(
            slug=f"pc-{tag}", name=f"model-c-{tag}", kind="model", paradigm="image_recon"
        )
        db.add_all([gen_a, gen_b, gen_c])
        db.flush()

        t1, t2 = _mk_task(db), _mk_task(db)
        db.add_all(
            [
                CommissionAttempt(
                    task_id=t1,
                    model_id=gen_a.name,
                    generator_id=gen_a.id,
                    status="ok",
                    mesh_stats_json=json.dumps({"vertices": 100}),
                ),
                CommissionAttempt(
                    task_id=t2,
                    model_id=gen_a.name,
                    generator_id=gen_a.id,
                    status="ok",
                    mesh_stats_json=json.dumps({"vertices": 300}),
                ),
                CommissionAttempt(
                    task_id=t1,
                    model_id=gen_b.name,
                    generator_id=gen_b.id,
                    status="ok",
                    mesh_stats_json=json.dumps({"vertices": 50}),
                ),
                CommissionAttempt(
                    task_id=t2,
                    model_id=gen_b.name,
                    generator_id=gen_b.id,
                    status="error",
                    mesh_stats_json="{}",
                ),
                CommissionAttempt(
                    task_id=t1,
                    model_id=gen_c.name,
                    generator_id=gen_c.id,
                    status="ok",
                    mesh_stats_json=json.dumps({"vertices": 999}),
                ),
            ]
        )
        # A commissioned output for gen_a with a plant scope + trait verdicts.
        out = ModelOutput(
            task_id=t1,
            generator_id=gen_a.id,
            asset_path=f"commissioned/{tag}.glb",
            source="commissioned",
        )
        db.add(out)
        db.flush()
        db.add(
            ModelScope(
                output_id=out.id,
                is_plant=True,
                parts_json=json.dumps(["whole_plant"]),
                judge_model="j",
            )
        )
        # presence class => is_assessable True regardless of parts.
        db.add_all(
            [
                TraitVerdict(
                    output_id=out.id,
                    rubric_id=0,
                    trait_key="k1",
                    trait_class="presence",
                    verdict="present_correct",
                ),
                TraitVerdict(
                    output_id=out.id,
                    rubric_id=0,
                    trait_key="k2",
                    trait_class="presence",
                    verdict="present_wrong",
                ),
                TraitVerdict(
                    output_id=out.id,
                    rubric_id=0,
                    trait_key="k3",
                    trait_class="presence",
                    verdict="not_assessable",
                ),
            ]
        )
        db.commit()

        rows = service.procedural_scorecard(db)
        by_model = {r["model"]: r for r in rows}

        assert gen_c.name not in by_model  # non-procedural_llm excluded

        a = by_model[gen_a.name]
        assert a["attempts"] == 2 and a["valid"] == 2
        assert a["pass_at_1"] == 1.0
        assert a["median_verts"] == 200  # median(100, 300)
        # 2 assessable (present_correct + present_wrong), na dropped; 1 correct.
        assert a["morph_correct"] == 1 and a["morph_assessable"] == 2
        assert a["morph_fidelity"] == 0.5
        assert a["n"] == 2

        b = by_model[gen_b.name]
        assert b["attempts"] == 2 and b["valid"] == 1
        assert b["pass_at_1"] == 0.5
        assert b["morph_fidelity"] is None  # no verdicts
        assert b["median_verts"] == 50

        # Ranked by pass_at_1 desc: A (1.0) before B (0.5).
        model_order = [r["model"] for r in rows if r["model"] in (gen_a.name, gen_b.name)]
        assert model_order == [gen_a.name, gen_b.name]


def test_scorecard_empty_when_no_procedural_generators():
    with SessionLocal() as db:
        # A generator with a non-procedural paradigm and no attempts must not appear;
        # the list may legitimately contain other tests' rows, so assert our tag is absent.
        tag = uuid.uuid4().hex
        g = Generator(slug=f"pe-{tag}", name=f"m-{tag}", kind="model", paradigm="retrieval")
        db.add(g)
        db.commit()
        rows = service.procedural_scorecard(db)
        assert all(r["model"] != g.name for r in rows)


def test_scorecard_morph_fidelity_0_sorts_before_none():
    """Test that real morph_fidelity == 0.0 sorts BEFORE None.

    When two generators have the same pass_at_1 (tiebreak), the one with
    morph_fidelity == 0.0 (a real computed value where all traits are wrong)
    must sort before the one with morph_fidelity == None (no trait verdicts).

    This tests the fix for the bug where `r["morph_fidelity"] or -1.0`
    incorrectly treated 0.0 as falsy and mapped both 0.0 and None to -1.0.
    """
    with SessionLocal() as db:
        tag = uuid.uuid4().hex
        # Generator B: morph_fidelity == None (no verdicts) — added FIRST to test stable sort.
        gen_b = Generator(
            slug=f"fid0-b-{tag}",
            name=f"model-fidelity-none-{tag}",
            kind="model",
            paradigm="procedural_llm",
        )
        # Generator A: morph_fidelity == 0.0 (1 assessable, 0 correct) — added SECOND.
        gen_a = Generator(
            slug=f"fid0-a-{tag}",
            name=f"model-fidelity-0-{tag}",
            kind="model",
            paradigm="procedural_llm",
        )
        db.add_all([gen_b, gen_a])
        db.flush()

        t1, t2 = _mk_task(db), _mk_task(db)

        # Both generators: 1/1 ok attempts → pass@1 == 1.0 (same tiebreak position).
        db.add_all(
            [
                CommissionAttempt(
                    task_id=t1,
                    model_id=gen_b.name,
                    generator_id=gen_b.id,
                    status="ok",
                    mesh_stats_json=json.dumps({"vertices": 100}),
                ),
                CommissionAttempt(
                    task_id=t2,
                    model_id=gen_a.name,
                    generator_id=gen_a.id,
                    status="ok",
                    mesh_stats_json=json.dumps({"vertices": 100}),
                ),
            ]
        )

        # Gen B: commissioned output with a scope but no trait verdicts → fidelity None.
        out_b = ModelOutput(
            task_id=t1,
            generator_id=gen_b.id,
            asset_path=f"commissioned/fid0-b-{tag}.glb",
            source="commissioned",
        )
        db.add(out_b)
        db.flush()
        db.add(
            ModelScope(
                output_id=out_b.id,
                is_plant=True,
                parts_json=json.dumps(["whole_plant"]),
                judge_model="j",
            )
        )
        # No TraitVerdicts for gen_b → morph_fidelity will be None.

        # Gen A: commissioned output with a scope + one assessable trait verdict (wrong).
        out_a = ModelOutput(
            task_id=t2,
            generator_id=gen_a.id,
            asset_path=f"commissioned/fid0-a-{tag}.glb",
            source="commissioned",
        )
        db.add(out_a)
        db.flush()
        db.add(
            ModelScope(
                output_id=out_a.id,
                is_plant=True,
                parts_json=json.dumps(["whole_plant"]),
                judge_model="j",
            )
        )
        db.add(
            TraitVerdict(
                output_id=out_a.id,
                rubric_id=0,
                trait_key="test_key",
                trait_class="presence",
                verdict="present_wrong",  # 0 correct, 1 assessable → fidelity 0.0
            )
        )

        db.commit()

        rows = service.procedural_scorecard(db)
        by_model = {r["model"]: r for r in rows}

        a_row = by_model[gen_a.name]
        b_row = by_model[gen_b.name]

        # Both have same pass_at_1.
        assert a_row["pass_at_1"] == 1.0
        assert b_row["pass_at_1"] == 1.0

        # A has morph_fidelity == 0.0, B has None.
        assert a_row["morph_fidelity"] == 0.0
        assert b_row["morph_fidelity"] is None

        # A must come before B in the ranked list.
        model_order = [r["model"] for r in rows if r["model"] in (gen_a.name, gen_b.name)]
        assert model_order == [gen_a.name, gen_b.name], (
            f"Expected {gen_a.name} (fidelity=0.0) before {gen_b.name} (fidelity=None), "
            f"got {model_order}"
        )
