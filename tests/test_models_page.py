"""TDD for the Models index (`/models`) + detail (`/models/{slug}`) pages (Task 12).

Real generators only (no fabricated org/company field — `Generator` has none); stats are
reused from `service.coverage_summary` + `_leaderboard_rows`, not hand-rolled.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_models_index_200_lists_known_generator():
    r = client.get("/models")
    assert r.status_code == 200
    assert "Generator Alpha" in r.text


def test_models_index_has_scope_pill_and_stats():
    r = client.get("/models")
    assert r.status_code == 200
    assert "b3d-scope-pill" in r.text


def test_model_detail_200_for_known_slug():
    r = client.get("/models/gen-alpha")
    assert r.status_code == 200
    assert "Generator Alpha" in r.text


def test_model_detail_404_for_unknown_slug():
    r = client.get("/models/nonexistent-generator-slug")
    assert r.status_code == 404
