# tests/test_semantic_pool.py
from __future__ import annotations

import uuid

import pytest

from app import admissibility, config
from app.database import SessionLocal, init_db
from app.models import Admissibility, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _rejected_semantic_output(db):
    g = Generator(slug=f"sp-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"sp-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
    db.add(o)
    db.flush()
    db.add(
        Admissibility(
            output_id=o.id,
            predicate="semantic",
            admit=False,
            reason="multiple",
            version="semantic-v1",
        )
    )
    db.commit()
    return o.id


def test_default_rubric_unchanged():
    assert admissibility.DEFAULT_RUBRIC == ["structural", "completeness"]


def test_direct_semantic_rubric_rejects_regardless_of_mode(monkeypatch):
    with SessionLocal() as db:
        oid = _rejected_semantic_output(db)
        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "off")
        assert oid in admissibility.non_admitted_output_ids(db, rubric=["semantic"])


def test_default_gate_includes_semantic_only_in_gate_mode(monkeypatch):
    with SessionLocal() as db:
        oid = _rejected_semantic_output(db)

        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "gate")
        assert oid in admissibility.non_admitted_output_ids(db)  # rubric=None

        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "advisory")
        assert oid not in admissibility.non_admitted_output_ids(db)

        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "off")
        assert oid not in admissibility.non_admitted_output_ids(db)


def test_effective_rubric_appends_semantic_in_gate(monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "gate")
    assert admissibility._effective_rubric() == ["structural", "completeness", "semantic"]
    monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "advisory")
    assert admissibility._effective_rubric() == ["structural", "completeness"]


def test_valid_semantic_mode_fails_loud_on_unknown():
    assert config._valid_semantic_mode("gate") == "gate"
    assert config._valid_semantic_mode("advisory") == "advisory"
    assert config._valid_semantic_mode("off") == "off"
    with pytest.raises(ValueError):
        config._valid_semantic_mode("gated")
