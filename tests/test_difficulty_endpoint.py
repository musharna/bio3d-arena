from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app


def setup_module(_m):
    init_db()


def test_difficulty_endpoint_returns_scorecard_shape():
    client = TestClient(app)
    resp = client.get("/api/difficulty.json")
    assert resp.status_code == 200
    body = resp.json()
    assert "scorecard" in body
    tiers = [c["tier"] for c in body["scorecard"]]
    assert tiers == ["easy", "moderate", "hard", "untiered"]
    for c in body["scorecard"]:
        assert isinstance(c["rows"], list)
