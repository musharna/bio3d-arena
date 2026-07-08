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


def test_models_index_omits_scope_pill_per_design():
    # Design (04-models.png): the Models title carries NO kingdom scope pill — the pill is
    # data-page chrome, and Models spans all generators regardless of kingdom scope.
    r = client.get("/models")
    assert r.status_code == 200
    assert "b3d-scope-pill" not in r.text


def test_model_detail_200_for_known_slug():
    r = client.get("/models/gen-alpha")
    assert r.status_code == 200
    assert "Generator Alpha" in r.text


def test_model_detail_404_for_unknown_slug():
    r = client.get("/models/nonexistent-generator-slug")
    assert r.status_code == 404


def test_models_rated_only_default_with_show_all_toggle():
    """Default grid hides never-voted (0-vote) generators; ?show_all=true reveals them, and
    the toggle is offered whenever anything is hidden."""
    default = client.get("/models")
    show_all = client.get("/models?show_all=true")
    assert default.status_code == 200 and show_all.status_code == 200
    n_default = default.text.count('class="b3d-model-card"')
    n_all = show_all.text.count('class="b3d-model-card"')
    assert n_all >= n_default
    if n_all > n_default:
        assert "Show all" in default.text
        assert "Show rated only" in show_all.text
