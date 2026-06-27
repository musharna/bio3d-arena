from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app
from app.models import (
    CalibrationPair,
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    Vote,
)


def setup_module(_m):
    init_db()


def _seed_calibration(db):
    for t in (Vote, Comparison, CalibrationPair):
        db.query(t).delete()
    db.commit()
    tag = uuid.uuid4().hex[:8]
    cat = Category(slug=f"cm-cat-{tag}", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    g1 = Generator(slug=f"cm-g1-{tag}", name="G1")
    g2 = Generator(slug=f"cm-g2-{tag}", name="G2")
    db.add_all([g1, g2])
    db.flush()
    task = Task(category_id=cat.id, title="cm-task", prompt="p")
    db.add(task)
    db.flush()
    oa = ModelOutput(task_id=task.id, generator_id=g1.id, asset_path=f"seed/{tag}_a.glb")
    ob = ModelOutput(task_id=task.id, generator_id=g2.id, asset_path=f"seed/{tag}_b.glb")
    db.add_all([oa, ob])
    db.flush()
    db.add(
        CalibrationPair(task_id=task.id, output_a_id=oa.id, output_b_id=ob.id, criterion_id=crit.id)
    )
    db.commit()


def test_calibration_mode_serves_pair_with_progress():
    with SessionLocal() as db:
        _seed_calibration(db)
    client = TestClient(app)
    r = client.get("/api/next?set=calibration")
    assert r.status_code == 200
    body = r.json()
    assert body["set"] == "calibration"
    assert body["progress"] == {"voted": 0, "total": 1}
    assert "comparison_id" in body


def test_calibration_mode_reports_done_after_voting_all():
    client = TestClient(app)
    with SessionLocal() as db:
        _seed_calibration(db)
    first = client.get("/api/next?set=calibration").json()
    client.post(
        "/api/vote?set=calibration", json={"comparison_id": first["comparison_id"], "winner": "a"}
    )
    nxt = client.get("/api/next?set=calibration").json()
    assert nxt.get("done") is True
    assert nxt["progress"]["voted"] == 1
    assert nxt["progress"]["total"] == 1
