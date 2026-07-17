from __future__ import annotations

import json
import uuid

from app import judge, service
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


def _attempt(task_id: int, gen, status: str, verts: int | None = None) -> CommissionAttempt:
    """An attempt row under the CURRENT protocol.

    protocol="repair" is not decoration: the scorecard excludes protocol="legacy" rows (the first
    harness scored its own export boilerplate and filed crashed scripts as empty meshes), and
    "legacy" is the column default — so a fixture that omits it seeds a row the board will never
    count, and the test quietly stops testing what it claims to.

    status_oneshot == status here because these fixtures are about the scorecard's arithmetic. The
    case where the two differ — failed unaided, passed with the traceback handed back — is the
    subject of tests/test_commission_protocol.py.
    """
    return CommissionAttempt(
        task_id=task_id,
        model_id=gen.name,
        generator_id=gen.id,
        status=status,
        status_oneshot=status,
        rounds=1,
        protocol="repair",
        mesh_stats_json=json.dumps({"vertices": verts}) if verts is not None else "{}",
    )


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
                _attempt(t1, gen_a, "ok", 100),
                _attempt(t2, gen_a, "ok", 300),
                _attempt(t1, gen_b, "ok", 50),
                _attempt(t2, gen_b, "error"),
                _attempt(t1, gen_c, "ok", 999),
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
                    judge_model=judge.JUDGE_MODEL,
                ),
                TraitVerdict(
                    output_id=out.id,
                    rubric_id=0,
                    trait_key="k2",
                    trait_class="presence",
                    verdict="present_wrong",
                    judge_model=judge.JUDGE_MODEL,
                ),
                TraitVerdict(
                    output_id=out.id,
                    rubric_id=0,
                    trait_key="k3",
                    trait_class="presence",
                    verdict="not_assessable",
                    judge_model=judge.JUDGE_MODEL,
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
        db.add_all([_attempt(t1, gen_b, "ok", 100), _attempt(t2, gen_a, "ok", 100)])

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
                judge_model=judge.JUDGE_MODEL,
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


def test_procedural_scorecard_filters_by_judge_model():
    """Test that procedural_scorecard only counts verdicts from the default judge_model.

    When an output has two TraitVerdicts on the same trait with different judge_model values,
    only the default judge_model verdict should be counted (no double-counting).
    This mirrors the behavior of recompute_trait_scores.
    """
    with SessionLocal() as db:
        tag = uuid.uuid4().hex
        # Generator with procedural_llm paradigm
        gen = Generator(
            slug=f"jm-{tag}", name=f"model-jm-{tag}", kind="model", paradigm="procedural_llm"
        )
        db.add(gen)
        db.flush()

        t = _mk_task(db)
        db.add(_attempt(t, gen, "ok", 100))

        # Commissioned output with a plant scope
        out = ModelOutput(
            task_id=t,
            generator_id=gen.id,
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

        # Two TraitVerdicts on the same output and trait_key, but different judge_model values
        # One with the default judge_model, one with a different judge_model
        db.add_all(
            [
                TraitVerdict(
                    output_id=out.id,
                    rubric_id=0,
                    trait_key="trait_v1",
                    trait_class="presence",
                    verdict="present_correct",
                    judge_model=judge.JUDGE_MODEL,  # default
                ),
                TraitVerdict(
                    output_id=out.id,
                    rubric_id=0,
                    trait_key="trait_v1",  # same trait_key
                    trait_class="presence",
                    verdict="present_correct",
                    judge_model="other-judge-model",  # different judge_model
                ),
            ]
        )
        db.commit()

        rows = service.procedural_scorecard(db)
        by_model = {r["model"]: r for r in rows}

        gen_row = by_model[gen.name]
        # Only the default judge_model verdict should be counted: morph_assessable == 1, not 2
        assert gen_row["morph_assessable"] == 1, (
            f"Expected morph_assessable == 1 (only default judge_model counted), "
            f"got {gen_row['morph_assessable']}"
        )
        assert gen_row["morph_correct"] == 1


def test_scorecard_excludes_a_generator_with_no_current_protocol_attempts():
    """A procedural_llm generator whose only attempts are 'legacy' (a retired model, or one measured
    solely under the old harness) has NOT been measured under the reported protocol — the board
    excludes legacy rows, so it would otherwise show as a phantom all-zero 0/0 row (n=0, pass@1=0.0)
    that reads as 'this model scored zero' rather than 'this model was not measured'. It must not
    appear on the board at all."""
    with SessionLocal() as db:
        tag = uuid.uuid4().hex
        gen = Generator(
            slug=f"legacyonly-{tag}",
            name=f"model-legacyonly-{tag}",
            kind="model",
            paradigm="procedural_llm",
        )
        db.add(gen)
        db.flush()
        t = _mk_task(db)
        # Only a LEGACY attempt exists — the scorecard filters protocol=='legacy', so this
        # generator has zero counted attempts.
        db.add(
            CommissionAttempt(
                task_id=t,
                model_id=gen.name,
                generator_id=gen.id,
                status="ok",
                status_oneshot="ok",
                rounds=1,
                protocol="legacy",
            )
        )
        db.commit()

        rows = service.procedural_scorecard(db)
        assert all(r["model"] != gen.name for r in rows), (
            "a generator with only legacy attempts (0 counted) must not appear as an n=0 row"
        )
