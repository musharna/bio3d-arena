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

# The Tier-C page routes (trait/{id} tested separately — it takes a path param). /research is
# the hub that lists the rest; it is gated by the same dependency, so a public visitor cannot
# use it to enumerate the boards.
INTERNAL_PATHS = [
    "/benchmark",
    "/significance",
    "/difficulty",
    "/fidelity",
    "/procedural",
    "/research",
]

# The JSON APIs backing those pages carry the SAME sensitive data, so they must be gated too —
# otherwise the page 404s but `curl /api/fidelity.json` still leaks the full scorecard.
INTERNAL_APIS = [
    "/api/procedural.json",
    "/api/fidelity.json",
    "/api/significance",
    "/api/bias",
    "/api/benchmark",
    "/api/difficulty.json",
    "/api/trait_scores.json",
    "/api/traits.json",
]


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


def test_internal_apis_404_on_public_instance(client, monkeypatch):
    # The data endpoints must be gated, not just the HTML pages — else the page 404s but the
    # backing /api/*.json still returns the full scorecard on the public deploy.
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    for path in INTERNAL_APIS:
        r = client.get(path)
        assert r.status_code == 404, f"{path} must 404 on public instance, got {r.status_code}"


def test_internal_apis_served_on_internal_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    for path in INTERNAL_APIS:
        assert client.get(path).status_code != 404, f"{path} must be served on internal instance"


def test_internal_pages_served_on_internal_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    for path in INTERNAL_PATHS:
        r = client.get(path)
        assert r.status_code != 404, f"{path} must be served on internal instance, got 404"


def test_nav_strips_internal_links_on_public_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    html = client.get("/").text
    # /research is the hub that now holds these boards — it must be stripped too, else the
    # public nav advertises a door that 404s.
    for href in [
        'href="/benchmark"',
        'href="/significance"',
        'href="/difficulty"',
        'href="/research"',
    ]:
        assert href not in html, f"nav must not contain {href} on public instance"


def test_coverage_stays_public(client, monkeypatch):
    # Tier-B Coverage is still public — it just moved out of the sidebar and is reached
    # through /dataset now. Asserted on the route + its new parent rather than on nav.
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    assert client.get("/coverage").status_code == 200
    assert 'href="/coverage"' in client.get("/dataset").text


def test_nav_shows_research_hub_on_internal_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    assert 'href="/research"' in client.get("/").text
    # The boards are advertised one level in, from the hub.
    hub = client.get("/research").text
    for href in ['href="/benchmark"', 'href="/significance"', 'href="/difficulty"']:
        assert href in hub, f"research hub must contain {href} on internal instance"


def test_significance_crosslinks_hidden_on_public_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    assert 'href="/significance"' not in client.get("/methodology").text
    assert "significance &amp; full audit" not in client.get("/leaderboard").text


def test_significance_crosslinks_shown_on_internal_instance(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    assert 'href="/significance"' in client.get("/methodology").text
    assert "significance &amp; full audit" in client.get("/leaderboard").text
