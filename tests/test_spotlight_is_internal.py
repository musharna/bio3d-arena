"""Spotlight is an internal research surface, not a public page.

The subject spotlights are a hand-authored module constant (`spotlight.SPOTLIGHTS`), and every
one of the six is a PLANT. The public site's headline claim is three kingdoms, so on the live
deploy switching the global kingdom filter to Fungi or Animals rendered a top-level nav item
that led to an empty page:

    ?kingdom=plants  -> 6 subjects
    ?kingdom=fungi   -> 0
    ?kingdom=animals -> 0

It also cannot grow with the corpus: 6 subjects against 20 active tasks, and adding a taxon to
the corpus does not add it here — the list is a constant, not a query.

So it moves behind `INTERNAL_PAGES_ENABLED` with the other research surfaces (/benchmark,
/research, /difficulty, /procedural) rather than being deleted: the per-taxon deep dive is
genuinely useful internally, and the nav-IA rule for this project is hide, don't delete.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def public(monkeypatch):
    """The public posture: scoring off => internal pages disabled."""
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False)


@pytest.fixture
def internal(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True)


def test_spotlight_index_404s_on_the_public_instance(client, public):
    assert client.get("/spotlight").status_code == 404


def test_both_spotlight_routes_carry_the_internal_gate():
    """Asserted on the route table, not by fetching a subject page.

    `GET /spotlight/<slug>` already 404s in the test database for the ordinary reason that no
    such subject is seeded, so a status-code assertion there would pass with or without the
    gate — a test that cannot fail. Reading the dependency off the route is the real check, and
    it covers the DETAIL page, which is the one that would otherwise stay publicly reachable by
    direct URL after the index was hidden.
    """
    from app.main import app as fastapi_app
    from app.main import require_internal_pages

    gated = {
        r.path
        for r in fastapi_app.routes
        if getattr(r, "path", "").startswith("/spotlight")
        and any(d.dependency is require_internal_pages for d in getattr(r, "dependencies", []))
    }
    assert gated == {"/spotlight", "/spotlight/{slug}"}, gated


def test_spotlight_still_works_on_the_internal_instance(client, internal):
    """The positive control: the gate must hide the page publicly, not break it. Without this,
    a route that 404s for an unrelated reason would pass the two assertions above."""
    assert client.get("/spotlight").status_code == 200


def test_the_sitemap_does_not_advertise_a_page_that_404s(client, public):
    """/spotlight was in the sitemap. Gating it without removing it there would hand crawlers a
    URL the public instance answers with 404."""
    body = client.get("/sitemap.xml").text
    assert "/spotlight" not in body
    assert "/arena" in body  # positive control: the sitemap is still populated


def test_the_public_nav_does_not_link_to_it(client, public):
    """A nav link to a 404 is worse than no nav link."""
    html = client.get("/dataset").text
    assert 'href="/spotlight"' not in html


def test_the_internal_nav_still_links_to_it(client, internal):
    html = client.get("/dataset").text
    assert 'href="/spotlight"' in html
