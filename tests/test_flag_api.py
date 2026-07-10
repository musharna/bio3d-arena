# tests/test_flag_api.py
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import config, integrity
from app.database import SessionLocal
from app.main import app
from app.models import Generator, ModelOutput, Task

client = TestClient(app)


@pytest.fixture(autouse=True)
def _curator_instance(monkeypatch):
    """Flagging is a curator-only tool served on the internal instance — force it on so these
    endpoint tests don't depend on the ambient public/internal signal (RECON_SCORER_URL)."""
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True)


def _output():
    with SessionLocal() as db:
        g = Generator(slug=f"fa-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
        db.add(g)
        db.flush()
        t = Task(title=f"fa-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
        db.add(t)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
        db.add(o)
        db.commit()
        return o.id


def test_flag_records_and_dedups():
    integrity.reset_rate_limits()
    oid = _output()
    # Curator instance: FLAG_HIDE_THRESHOLD defaults to 1, so a single flag hides immediately.
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_the_organism"})
    assert r.status_code == 200 and r.json() == {"status": "ok", "hidden": True, "flags": 1}
    # same session (same TestClient cookie) again → no double count
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_the_organism"})
    assert r.json()["flags"] == 1


def test_flag_unknown_output_404():
    integrity.reset_rate_limits()
    r = client.post("/api/flag", json={"output_id": 999999, "reason": "failed"})
    assert r.status_code == 404


def test_flag_bad_reason_422():
    integrity.reset_rate_limits()
    oid = _output()
    r = client.post("/api/flag", json={"output_id": oid, "reason": "banana"})
    assert r.status_code == 422


def test_flag_autohide_at_threshold():
    integrity.reset_rate_limits()
    oid = _output()
    from app import flags

    with SessionLocal() as db:
        flags.record_flag(db, oid, "other-1", "failed", threshold=99)
        flags.record_flag(db, oid, "other-2", "failed", threshold=99)
        db.commit()
    # the pre-seeded flags used a high threshold (not hidden); this client flag uses the real
    # default and hides the output. count reflects all three distinct sessions.
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_the_organism"})
    assert r.json() == {"status": "ok", "hidden": True, "flags": 3}
    with SessionLocal() as db:
        assert db.get(ModelOutput, oid).hidden_at is not None
