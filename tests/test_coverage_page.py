"""/coverage HTML page + /api/coverage.json render and expose the disclosure data."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_coverage_page_renders():
    r = client.get("/coverage")
    assert r.status_code == 200
    body = r.text
    assert "Coverage &amp; governance" in body or "Coverage & governance" in body
    # governance policy claims are present
    assert "No silent deprecation" in body
    assert "least-compared" in body  # fairness claim


def test_coverage_json_shape():
    r = client.get("/api/coverage.json")
    assert r.status_code == 200
    data = r.json()
    assert "generators" in data and "tasks" in data
    assert isinstance(data["generators"], list) and isinstance(data["tasks"], list)
    if data["generators"]:
        g = data["generators"][0]
        for k in (
            "generator",
            "kind",
            "tasks",
            "outputs",
            "votes",
            "confidence",
            "excluded_from_mode_a",
        ):
            assert k in g
