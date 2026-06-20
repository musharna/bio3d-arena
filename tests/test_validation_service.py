"""Validation DB-wiring tests: /admin/revalidate batch, meta_json storage, and the
seeded 1CRN reference demo producing real RMSD/TM-score numbers."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_revalidate_requires_token():
    assert client.post("/admin/revalidate", data={"token": "wrong"}).status_code == 401


def test_revalidate_populates_molecular_output_meta():
    resp = client.post("/admin/revalidate", data={"token": "test-token"})
    assert resp.status_code == 200
    detail = resp.json()["detail"]
    assert detail["molecular"] >= 1  # at least the 1CRN / ligand structures
    assert detail["outputs"] >= detail["molecular"]


def test_reference_demo_produces_rmsd_and_tm():
    client.post("/admin/revalidate", data={"token": "test-token"})
    from app.database import SessionLocal
    from app.models import ModelOutput, Task

    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.reference_asset_id.isnot(None)).first()
        assert task is not None, "expected a seeded reference task"
        outs = db.query(ModelOutput).filter(ModelOutput.task_id == task.id).all()
        refs = [json.loads(o.meta_json).get("validation", {}).get("reference") for o in outs]
        ok = [r for r in refs if r and r.get("status") == "ok"]
        assert ok, "expected at least one reference comparison with rmsd/tm"
        assert all("rmsd" in r and "tm_score" in r for r in ok)
    finally:
        db.close()


def test_validation_page_and_api_render():
    client.post("/admin/revalidate", data={"token": "test-token"})
    html = client.get("/validation").text
    assert "Physical validity" in html
    assert "independent of the human aesthetic vote" in html
    api = client.get("/api/validation").json()
    assert "validity_leaderboard" in api
    assert any(row["n_molecular"] >= 1 for row in api["validity_leaderboard"])
    assert api["reference_accuracy"], "expected reference accuracy rows"


def test_validation_in_nav():
    html = client.get("/").text
    nav = html.split("<nav>")[1].split("</nav>")[0]
    assert ">Validation<" in nav
