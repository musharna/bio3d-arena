# tests/test_trait_scoring.py
from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import TraitCalibration, TraitScore, TraitVerdict


def setup_module(_m):
    init_db()


_OIDS = [9001, 9002, 9003, 9004]


def _clear(db):
    db.query(TraitScore).filter(TraitScore.output_id.in_(_OIDS)).delete(False)
    db.query(TraitVerdict).filter(TraitVerdict.output_id.in_(_OIDS)).delete(False)
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
                    output_id=9001,
                    rubric_id=1,
                    trait_key=f"k{i}",
                    trait_class=cls,
                    verdict=v,
                    judge_model="m",
                )
            )
        db.commit()
        service.recompute_trait_scores(db, judge_model="m")
        ts = db.query(TraitScore).filter_by(output_id=9001).one()
        # accepted+assessable color verdicts: 1 correct of 2 → 0.5
        assert ts.n_scored == 2 and ts.botanical_accuracy == 0.5


def test_calibration_gate_threshold():
    with SessionLocal() as db:
        _clear(db)
        labels = [(9002, "k", "color", "present_correct")]
        db.add(
            TraitVerdict(
                output_id=9002,
                rubric_id=1,
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
                    output_id=9004,
                    rubric_id=1,
                    trait_key=f"a{i}",
                    trait_class="color",
                    verdict=v,
                    judge_model="m",
                )
            )
            labels.append((9004, f"a{i}", "color", v))  # human == VLM (perfect)
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
                output_id=9003,
                rubric_id=1,
                trait_key="c",
                trait_class="color",
                verdict="present_correct",
                judge_model="A",
            )
        )
        db.add(
            TraitVerdict(
                output_id=9003,
                rubric_id=1,
                trait_key="c",
                trait_class="color",
                verdict="present_wrong",
                judge_model="B",
            )
        )
        db.commit()
        service.recompute_trait_scores(db, judge_model="A")
        ts = db.query(TraitScore).filter_by(output_id=9003).one()
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
        db.query(Task).filter(Task.title.like("tta-%")).delete(False)
        db.query(Generator).filter(Generator.slug.like("tta-%")).delete(False)
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
