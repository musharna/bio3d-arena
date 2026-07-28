# tests/test_trait_scoring.py
from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import ModelScope, TraitCalibration, TraitScore, TraitVerdict
from tests.factories import cascade_delete, make_outputs, make_rubric

# These were the literals 9001..9004 and rubric_id=1 — ids of rows that never existed. FK
# enforcement refuses a child pointing at a missing parent, so the fixture now mints real
# outputs and a real rubric once per module and the tests key off their ids.
_OIDS: list[int] = []
_RUBRIC_ID = 0


def setup_module(_m):
    init_db()
    global _RUBRIC_ID
    with SessionLocal() as db:
        outs = make_outputs(db, 4)
        _OIDS[:] = [o.id for o in outs]
        _RUBRIC_ID = make_rubric(db, task_id=outs[0].task_id).id


def _clear(db):
    db.query(TraitScore).filter(TraitScore.output_id.in_(_OIDS)).delete(False)
    db.query(TraitVerdict).filter(TraitVerdict.output_id.in_(_OIDS)).delete(False)
    db.query(ModelScope).filter(ModelScope.output_id.in_(_OIDS)).delete(False)
    db.query(TraitCalibration).delete(False)
    db.commit()


def test_scores_use_only_accepted_classes_and_skip_not_assessable():
    with SessionLocal() as db:
        _clear(db)
        # color is accepted; phyllotaxy is NOT calibrated → excluded from the score
        db.add(TraitCalibration(trait_class="color", kappa=0.8, n=30, accepted=True))
        vs = [
            ("color", "present_correct"),
            ("color", "absent"),
            ("color", "not_assessable"),  # excluded
            ("phyllotaxy", "present_correct"),  # excluded (class not accepted)
        ]
        for i, (cls, v) in enumerate(vs):
            db.add(
                TraitVerdict(
                    output_id=_OIDS[0],
                    rubric_id=_RUBRIC_ID,
                    trait_key=f"k{i}",
                    trait_class=cls,
                    verdict=v,
                    judge_model="m",
                )
            )
        db.commit()
        service.recompute_trait_scores(db, judge_model="m")
        ts = db.query(TraitScore).filter_by(output_id=_OIDS[0]).one()
        # accepted+assessable color verdicts: 1 correct of 2 → 0.5
        assert ts.n_scored == 2 and ts.botanical_accuracy == 0.5


def test_scores_gate_on_model_scope():
    """A verdict is scored only if its trait is assessable on what the model depicts. On a
    single-fruit model, fruit_form counts but a VLM 'habit present_correct' over-read is dropped
    — so the model can't earn botanical accuracy on a plant structure it doesn't show."""
    with SessionLocal() as db:
        _clear(db)
        db.add(TraitCalibration(trait_class="organ_shape", kappa=0.8, n=30, accepted=True))
        db.add(TraitCalibration(trait_class="habit", kappa=0.8, n=30, accepted=True))
        db.add(
            ModelScope(output_id=_OIDS[0], is_plant=True, parts_json='["fruit"]', judge_model="m")
        )
        for key, cls in [("fruit_form", "organ_shape"), ("plant_habit", "habit")]:
            db.add(
                TraitVerdict(
                    output_id=_OIDS[0],
                    rubric_id=_RUBRIC_ID,
                    trait_key=key,
                    trait_class=cls,
                    verdict="present_correct",
                    judge_model="m",
                )
            )
        db.commit()
        service.recompute_trait_scores(db, judge_model="m")
        ts = db.query(TraitScore).filter_by(output_id=_OIDS[0]).one()
        # only fruit_form is assessable on a fruit-only model; habit dropped by scope
        assert ts.n_scored == 1 and ts.botanical_accuracy == 1.0


def test_calibration_gates_on_model_scope():
    """A human/VLM pair is dropped from kappa when the trait isn't assessable on the model's
    scope — so a VLM habit over-read on a single-fruit model doesn't enter calibration either."""
    with SessionLocal() as db:
        _clear(db)
        db.add(
            ModelScope(output_id=_OIDS[1], is_plant=True, parts_json='["fruit"]', judge_model="m")
        )
        rows = [
            ("fruit_form", "organ_shape", "present_correct", "present_correct"),  # kept
            ("plant_habit", "habit", "not_assessable", "present_correct"),  # scope drops
        ]
        labels = []
        for key, cls, human, vlm in rows:
            db.add(
                TraitVerdict(
                    output_id=_OIDS[1],
                    rubric_id=_RUBRIC_ID,
                    trait_key=key,
                    trait_class=cls,
                    verdict=vlm,
                    judge_model="m",
                )
            )
            labels.append((_OIDS[1], key, cls, human))
        db.commit()
        service.recompute_trait_calibration(db, labels, judge_model="m")
        assert db.query(TraitCalibration).filter_by(trait_class="organ_shape").one().n == 1
        assert db.query(TraitCalibration).filter_by(trait_class="habit").one_or_none() is None


def test_calibration_gate_threshold():
    with SessionLocal() as db:
        _clear(db)
        labels = [(_OIDS[1], "k", "color", "present_correct")]
        db.add(
            TraitVerdict(
                output_id=_OIDS[1],
                rubric_id=_RUBRIC_ID,
                trait_key="k",
                trait_class="color",
                verdict="present_correct",
                judge_model="m",
            )
        )
        db.commit()
        # n below MIN_N → not accepted even at perfect agreement
        res = service.recompute_trait_calibration(db, labels, judge_model="m")
        cal = db.query(TraitCalibration).filter_by(trait_class="color").one()
        assert cal.accepted is False  # n=1 < MODE_C_MIN_N
        assert res["classes"] == 1


def test_calibration_drops_vlm_not_assessable_but_keeps_human_na_overreads():
    """Dropping from kappa is ASYMMETRIC and aligned with scoring. A pair whose VLM verdict is
    not_assessable is dropped (the VLM made no scoreable call — scoring excludes it too). But a
    pair whose HUMAN verdict is not_assessable while the VLM made a scoreable call is KEPT: that
    is the VLM over-reading a trait the model doesn't depict (e.g. habit on a single-fruit
    tomato), and scoring COUNTS that verdict — so it must count against agreement, not vanish."""
    with SessionLocal() as db:
        _clear(db)
        rows = [
            ("k0", "present_correct", "present_correct"),  # kept (both scoreable, agree)
            ("k1", "absent", "not_assessable"),  # dropped (VLM made no scoreable call)
            ("k2", "not_assessable", "absent"),  # KEPT (VLM over-read; disagreement)
            ("k3", "not_assessable", "not_assessable"),  # dropped (VLM not_assessable)
        ]
        labels = []
        for key, human, vlm in rows:
            db.add(
                TraitVerdict(
                    output_id=_OIDS[2],
                    rubric_id=_RUBRIC_ID,
                    trait_key=key,
                    trait_class="color",
                    verdict=vlm,
                    judge_model="m",
                )
            )
            labels.append((_OIDS[2], key, "color", human))
        db.commit()
        service.recompute_trait_calibration(db, labels, judge_model="m")
        cal = db.query(TraitCalibration).filter_by(trait_class="color").one()
        # k0 (agree) + k2 (VLM over-read, kept as disagreement); k1 & k3 dropped (VLM na)
        assert cal.n == 2


def test_calibration_gate_accepts_above_threshold():
    """The positive path: >=MIN_N paired labels over >=2 categories at perfect agreement
    (kappa=1.0) must flip accepted -> True. This is the trust gate Mode-C relies on."""
    with SessionLocal() as db:
        _clear(db)
        labels = []
        for i in range(30):
            v = "present_correct" if i % 2 == 0 else "absent"  # 2 categories, 15/15
            db.add(
                TraitVerdict(
                    output_id=_OIDS[3],
                    rubric_id=_RUBRIC_ID,
                    trait_key=f"a{i}",
                    trait_class="color",
                    verdict=v,
                    judge_model="m",
                )
            )
            labels.append((_OIDS[3], f"a{i}", "color", v))  # human == VLM (perfect)
        db.commit()
        service.recompute_trait_calibration(db, labels, judge_model="m")
        cal = db.query(TraitCalibration).filter_by(trait_class="color").one()
        assert cal.n == 30 and cal.kappa == 1.0 and cal.accepted is True


def test_scores_partition_by_judge_model():
    """Two judge models verdicting the same (output, trait) must not merge: scoring one
    model sees only that model's verdict (no double-count, no last-write-win)."""
    with SessionLocal() as db:
        _clear(db)
        db.add(TraitCalibration(trait_class="color", kappa=0.9, n=30, accepted=True))
        # Model A says correct, model B says wrong, on the SAME trait of the SAME output.
        db.add(
            TraitVerdict(
                output_id=_OIDS[2],
                rubric_id=_RUBRIC_ID,
                trait_key="c",
                trait_class="color",
                verdict="present_correct",
                judge_model="A",
            )
        )
        db.add(
            TraitVerdict(
                output_id=_OIDS[2],
                rubric_id=_RUBRIC_ID,
                trait_key="c",
                trait_class="color",
                verdict="present_wrong",
                judge_model="B",
            )
        )
        db.commit()
        service.recompute_trait_scores(db, judge_model="A")
        ts = db.query(TraitScore).filter_by(output_id=_OIDS[2]).one()
        assert ts.n_total == 1 and ts.n_scored == 1
        assert ts.botanical_accuracy == 1.0 and ts.judge_model == "A"


def test_tier_trait_accuracy_groups_scored_outputs_by_tier():
    """Per-tier mean botanical accuracy: two scored outputs in an 'easy' task average to the
    tier mean; gold and None-accuracy outputs are excluded."""
    from app.difficulty import set_task_difficulty
    from app.models import Category, Generator, ModelOutput, Task

    with SessionLocal() as db:
        olds = db.query(ModelOutput).filter(ModelOutput.asset_path.like("tta/%.glb")).all()
        db.query(TraitScore).filter(TraitScore.output_id.in_([o.id for o in olds])).delete(False)
        db.query(ModelOutput).filter(ModelOutput.asset_path.like("tta/%.glb")).delete(False)
        cascade_delete(db, Task, Task.title.like("tta-%"))
        cascade_delete(db, Generator, Generator.slug.like("tta-%"))
        db.query(Category).filter_by(slug="tta-cat").delete(False)
        db.commit()

        cat = Category(slug="tta-cat", name="TTA-Cat")
        # Unique name: a generic name like "Gen" would trip generator_display_names
        # disambiguation and rename other tests' same-named generators.
        gen = Generator(slug="tta-g", name="TTA-Gen")
        db.add_all([cat, gen])
        db.flush()
        easy = Task(category_id=cat.id, title="tta-easy", prompt="p")
        db.add(easy)
        db.flush()
        o1 = ModelOutput(task_id=easy.id, generator_id=gen.id, asset_path="tta/1.glb")
        o2 = ModelOutput(task_id=easy.id, generator_id=gen.id, asset_path="tta/2.glb")
        og = ModelOutput(task_id=easy.id, generator_id=gen.id, asset_path="tta/g.glb", is_gold=True)
        db.add_all([o1, o2, og])
        db.flush()
        db.add_all(
            [
                TraitScore(output_id=o1.id, botanical_accuracy=0.4, n_scored=2, n_total=3),
                TraitScore(output_id=o2.id, botanical_accuracy=0.6, n_scored=2, n_total=2),
                TraitScore(output_id=og.id, botanical_accuracy=1.0, n_scored=1, n_total=1),
            ]
        )
        set_task_difficulty(db, easy.id, "easy", "x", commit=False)
        db.commit()

        rows = service.tier_trait_accuracy(db)
        easy_rows = [r for r in rows if r["tier"] == "easy"]
        assert len(easy_rows) == 1
        # gold excluded → only o1,o2 → mean 0.5 over 2 outputs
        assert easy_rows[0]["n_outputs"] == 2 and easy_rows[0]["mean_accuracy"] == 0.5
