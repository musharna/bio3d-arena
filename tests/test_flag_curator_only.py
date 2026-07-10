"""Flagging is a curator-only tool on the INTERNAL instance, not a public feature:
1. a single flag hides immediately (FLAG_HIDE_THRESHOLD defaults to 1),
2. POST /api/flag 404s on the public deploy (require_internal_pages gate),
3. the arena exposes data-can-flag by instance so the client only renders the ⚑ button
   (and only calls /api/flag) on the internal instance.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, integrity
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Generator, ModelOutput, Task

client = TestClient(app)


def _output() -> int:
    init_db()
    with SessionLocal() as db:
        g = Generator(slug=f"cf-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
        db.add(g)
        db.flush()
        t = Task(title=f"cf-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
        db.add(t)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
        db.add(o)
        db.commit()
        return o.id


def test_flag_hide_threshold_defaults_to_one():
    if "BIO3D_FLAG_HIDE_THRESHOLD" in os.environ:
        pytest.skip("threshold overridden by env")
    assert config.FLAG_HIDE_THRESHOLD == 1


def test_single_flag_hides_immediately_on_curator_instance(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True)
    monkeypatch.setattr(config, "FLAG_HIDE_THRESHOLD", 1)
    integrity.reset_rate_limits()
    oid = _output()
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_the_organism"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "hidden": True, "flags": 1}
    with SessionLocal() as db:
        assert db.get(ModelOutput, oid).hidden_at is not None


def test_flag_endpoint_404_on_public_instance(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False)
    integrity.reset_rate_limits()
    oid = _output()
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_the_organism"})
    assert r.status_code == 404


def test_arena_exposes_can_flag_by_instance(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True)
    assert 'data-can-flag="true"' in client.get("/arena").text
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False)
    assert 'data-can-flag="false"' in client.get("/arena").text
