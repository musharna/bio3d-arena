# tests/test_trait_pages.py
from __future__ import annotations

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_trait_json_endpoints_shape():
    r = client.get("/api/trait_scores.json")
    assert r.status_code == 200
    data = r.json()
    assert "generators" in data and isinstance(data["generators"], list)
    r2 = client.get("/api/traits.json")
    assert r2.status_code == 200
    assert "rubrics" in r2.json()


def test_trait_scorecard_route_handles_missing_output():
    # unknown output → 404, not 500
    assert client.get("/trait/99999999").status_code == 404
