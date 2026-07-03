# tests/test_semantic_batch.py
from app.database import SessionLocal, init_db
from app.models import (
    Admissibility,
    Category,
    Generator,
    ModelOutput,
    OutputFlag,
    Task,
    TraitRubric,
)
from app.semantic import (
    SemanticPredicate,
    VERSION,
    enumerate_semantic_work,
    evaluate_outputs,
)


def setup_module(_m):
    init_db()


def _cat_gen(db):
    cat = db.query(Category).filter_by(slug="sem-batch").one_or_none()
    if cat is None:
        cat = Category(slug="sem-batch", name="Solanum lycopersicum")
        db.add(cat)
        db.flush()
    gen = db.query(Generator).filter_by(slug="sem-gen").one_or_none()
    if gen is None:
        gen = Generator(slug="sem-gen", name="sem-gen", kind="model", paradigm="")
        db.add(gen)
        db.flush()
    return cat, gen


def _output(db, cat, gen, *, taxon=None, is_gold=False, source="bio3d-arena"):
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    if taxon is not None:
        db.add(TraitRubric(task_id=task.id, taxon=taxon, traits_json="[]"))
    out = ModelOutput(
        task_id=task.id, generator_id=gen.id, asset_path="p.glb", is_gold=is_gold, source=source
    )
    db.add(out)
    db.flush()
    return out.id


class _FakeClient:
    """client.messages.create -> a record_admissibility tool_use with a fixed verdict."""

    def __init__(self, verdict):
        self._verdict = verdict
        self.messages = self

    def create(self, **kw):
        verdict = self._verdict

        class B:
            type = "tool_use"
            name = "record_admissibility"
            input = {"verdict": verdict, "note": "n"}

        class R:
            content = [B()]

        return R()


def test_enumerate_includes_taxonless_and_excludes_ineligible():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        taxonless = _output(db, cat, gen, taxon=None)
        with_taxon = _output(db, cat, gen, taxon="Solanum lycopersicum")
        gold = _output(db, cat, gen, is_gold=True)
        db.commit()
        work = enumerate_semantic_work(db)
        by_id = {w["output_id"]: w["taxon"] for w in work}
        assert taxonless in by_id and by_id[taxonless] is None
        assert with_taxon in by_id and by_id[with_taxon] == "Solanum lycopersicum"
        assert gold not in by_id


def test_enumerate_skips_current_version_verdicts():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid = _output(db, cat, gen, taxon=None)
        db.add(Admissibility(output_id=oid, predicate="semantic", admit=True, version=VERSION))
        db.commit()
        assert oid not in {w["output_id"] for w in enumerate_semantic_work(db)}


def test_evaluate_upserts_reject_verdict_and_fail_loud():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid_ok = _output(db, cat, gen, taxon="Solanum lycopersicum")
        oid_raise = _output(db, cat, gen, taxon=None)
        db.commit()

        def sheet_for(oid):
            if oid == oid_raise:
                raise RuntimeError("render failed")
            return b"\x89PNG"

        work = [
            {"output_id": oid_ok, "taxon": "Solanum lycopersicum"},
            {"output_id": oid_raise, "taxon": None},
        ]
        summary = evaluate_outputs(
            db, work, client=_FakeClient("multiple"), sheet_for=sheet_for, emit_flags=False
        )
        db.commit()
        assert summary["scored"] == 1 and summary["errors"] == 1
        row = db.query(Admissibility).filter_by(output_id=oid_ok, predicate="semantic").one()
        assert row.admit is False and row.reason == "multiple" and row.version == VERSION


def test_advisory_flag_is_non_hiding_and_only_on_reject():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid_bad = _output(db, cat, gen, taxon=None)
        oid_good = _output(db, cat, gen, taxon=None)
        db.commit()

        # reject + emit_flags -> exactly one flag, output NOT hidden
        evaluate_outputs(
            db,
            [{"output_id": oid_bad, "taxon": None}],
            client=_FakeClient("sub_part"),
            sheet_for=lambda o: b"\x89PNG",
            emit_flags=True,
        )
        db.commit()
        flags_bad = db.query(OutputFlag).filter_by(output_id=oid_bad).all()
        assert len(flags_bad) == 1 and flags_bad[0].reason == "sub_part"
        assert db.get(ModelOutput, oid_bad).hidden_at is None

        # ok verdict + emit_flags -> no flag
        evaluate_outputs(
            db,
            [{"output_id": oid_good, "taxon": None}],
            client=_FakeClient("ok"),
            sheet_for=lambda o: b"\x89PNG",
            emit_flags=True,
        )
        db.commit()
        assert db.query(OutputFlag).filter_by(output_id=oid_good).count() == 0


def test_emit_flags_false_upserts_but_does_not_flag():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid = _output(db, cat, gen, taxon=None)
        db.commit()
        evaluate_outputs(
            db,
            [{"output_id": oid, "taxon": None}],
            client=_FakeClient("not_a_plant"),
            sheet_for=lambda o: b"\x89PNG",
            emit_flags=False,
        )
        db.commit()
        assert (
            db.query(Admissibility).filter_by(output_id=oid, predicate="semantic").one().admit
            is False
        )
        assert db.query(OutputFlag).filter_by(output_id=oid).count() == 0


def test_semantic_predicate_reads_rejects():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid = _output(db, cat, gen, taxon=None)
        db.add(
            Admissibility(
                output_id=oid, predicate="semantic", admit=False, reason="multiple", version=VERSION
            )
        )
        db.commit()
        assert oid in SemanticPredicate().rejected_output_ids(db)
