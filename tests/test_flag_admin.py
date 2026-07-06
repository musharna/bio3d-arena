# tests/test_flag_admin.py
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Generator, ModelOutput, OutputFlag, Task, _utcnow

client = TestClient(app)
TOKEN = "test-token"


def _flagged_output(hidden=False):
    with SessionLocal() as db:
        g = Generator(slug=f"ad-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
        db.add(g)
        db.flush()
        t = Task(title=f"ad-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
        db.add(t)
        db.flush()
        o = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="x.glb",
            asset_format="glb",
            hidden_at=_utcnow() if hidden else None,
        )
        db.add(o)
        db.flush()
        db.add(OutputFlag(output_id=o.id, session_id="s1", reason="not_the_organism"))
        db.commit()
        return o.id


def test_hide_and_restore_round_trip():
    oid = _flagged_output(hidden=False)
    r = client.post(f"/admin/outputs/{oid}/hide", data={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(ModelOutput, oid).hidden_at is not None
    r = client.post(f"/admin/outputs/{oid}/restore", data={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(ModelOutput, oid).hidden_at is None


def test_admin_actions_require_token():
    oid = _flagged_output()
    assert client.post(f"/admin/outputs/{oid}/hide", data={"token": "wrong"}).status_code == 401


def test_moderation_page_lists_flagged():
    oid = _flagged_output(hidden=True)
    r = client.get(f"/admin/moderation?token={TOKEN}")
    assert r.status_code == 200 and str(oid) in r.text
