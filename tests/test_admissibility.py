# tests/test_admissibility.py
from __future__ import annotations

import uuid

import pytest

from app import admissibility
from app.database import SessionLocal, init_db
from app.models import Completeness, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _output_with_category(db, category):
    g = Generator(slug=f"ad-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"ad-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
    db.add(o)
    db.flush()
    db.add(Completeness(output_id=o.id, category=category, score=0.0, scorer_version="v1"))
    db.commit()
    return o.id


def test_completeness_predicate_rejects_bad_category():
    with SessionLocal() as db:
        bad = _output_with_category(db, "fragment")
        good = _output_with_category(db, "complete")
        rejected = admissibility.non_admitted_output_ids(db, rubric=["completeness"])
        assert bad in rejected and good not in rejected


def test_empty_rubric_admits_all():
    with SessionLocal() as db:
        assert admissibility.non_admitted_output_ids(db, rubric=[]) == set()


def test_unknown_predicate_is_fail_loud():
    with SessionLocal() as db:
        with pytest.raises(KeyError):
            admissibility.non_admitted_output_ids(db, rubric=["does_not_exist"])


def test_default_rubric_is_structural_then_completeness():
    assert admissibility.DEFAULT_RUBRIC == ["structural", "completeness"]
