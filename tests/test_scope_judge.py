# tests/test_scope_judge.py
from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.models import ModelScope
from scripts import scope_judge
from tests.factories import make_outputs


# These were the literals _OIDS[0]/_OIDS[1] — ids of outputs that never existed. FK enforcement
# refuses a model_scope row pointing at a missing output, so mint real ones.
_OIDS: list[int] = []


def setup_module(_m):
    init_db()
    with SessionLocal() as db:
        _OIDS[:] = [o.id for o in make_outputs(db, 2)]


def _clear(db):
    db.query(ModelScope).filter(ModelScope.output_id.in_(_OIDS)).delete(False)
    db.commit()


def test_run_batch_persists_scope_and_is_resumable():
    with SessionLocal() as db:
        _clear(db)
        work = [
            {"output_id": _OIDS[0], "species": "Solanum lycopersicum", "prompt": "a tomato"},
            {"output_id": _OIDS[1], "species": "Zea mays", "prompt": "a maize plant"},
        ]

        def classify_fn(species, prompt, b64):
            return {"is_plant": True, "visible_parts": ["fruit"], "rationale": "one fruit"}

        def sheet_b64(oid):
            return "ZmFrZQ=="  # injected: no browser render

        res = scope_judge.run_batch(
            db, classify_fn=classify_fn, sheet_b64=sheet_b64, work=work, judge_model="m"
        )
        assert res["written"] == 2 and res["errors"] == 0
        row = db.query(ModelScope).filter_by(output_id=_OIDS[0], judge_model="m").one()
        assert row.is_plant is True and json.loads(row.parts_json) == ["fruit"]

        # resumable: a second run skips already-scoped outputs (no re-spend)
        res2 = scope_judge.run_batch(
            db, classify_fn=classify_fn, sheet_b64=sheet_b64, work=work, judge_model="m"
        )
        assert res2["written"] == 0 and res2["skipped"] == 2


def test_run_batch_counts_errors_and_continues():
    with SessionLocal() as db:
        _clear(db)
        work = [
            {"output_id": _OIDS[0], "species": "Rosa", "prompt": "a rose"},
            {"output_id": _OIDS[1], "species": "Rosa", "prompt": "a rose"},
        ]

        def classify_fn(species, prompt, b64):
            if b64 == "boom":
                raise RuntimeError("render failed")
            return {"is_plant": True, "visible_parts": [], "rationale": ""}

        def sheet_b64(oid):
            return "boom" if oid == _OIDS[0] else "ok"

        res = scope_judge.run_batch(
            db, classify_fn=classify_fn, sheet_b64=sheet_b64, work=work, judge_model="m"
        )
        assert res["errors"] == 1 and res["written"] == 1
