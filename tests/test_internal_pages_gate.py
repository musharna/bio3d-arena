"""Tier-C internal research/analytics pages must be UNPUBLISHED on the public instance.

The public deploy runs with INTERNAL_PAGES_ENABLED=False (defaults to SCORING_ENABLED — the
same signal that keeps the Agrigen scorer out of public). When off, the six research pages
hard-404 and are stripped from nav + cross-links, so novel methodology never reaches the
public surface. On the internal instance (scorer present) everything is served as before.
"""

import pytest
from starlette.testclient import TestClient

from app import config
from app.main import app

# The six Tier-C page routes (trait/{id} tested separately — it takes a path param).
INTERNAL_PATHS = ["/benchmark", "/significance", "/difficulty", "/fidelity", "/procedural"]


@pytest.fixture
def client():
    return TestClient(app)


def test_internal_pages_404_on_public_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    for path in INTERNAL_PATHS:
        r = client.get(path)
        assert r.status_code == 404, f"{path} must 404 on public instance, got {r.status_code}"


def test_trait_route_404_on_public_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    # Guard runs before the handler, so it 404s regardless of whether output 1 exists.
    assert client.get("/trait/1").status_code == 404


def test_internal_pages_served_on_internal_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    for path in INTERNAL_PATHS:
        r = client.get(path)
        assert r.status_code != 404, f"{path} must be served on internal instance, got 404"


def test_nav_strips_internal_links_on_public_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    html = client.get("/").text
    for href in ['href="/benchmark"', 'href="/significance"', 'href="/difficulty"']:
        assert href not in html, f"nav must not contain {href} on public instance"
    # Tier-B Coverage stays public.
    assert 'href="/coverage"' in html


def test_nav_shows_internal_links_on_internal_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    html = client.get("/").text
    for href in ['href="/benchmark"', 'href="/significance"', 'href="/difficulty"']:
        assert href in html, f"nav must contain {href} on internal instance"


def test_significance_crosslinks_hidden_on_public_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    assert 'href="/significance"' not in client.get("/methodology").text
    assert "significance &amp; full audit" not in client.get("/leaderboard").text


def test_significance_crosslinks_shown_on_internal_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    assert 'href="/significance"' in client.get("/methodology").text
    assert "significance &amp; full audit" in client.get("/leaderboard").text
