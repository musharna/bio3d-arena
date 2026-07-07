"""Accessibility + trust-chrome regression tests (Increment 3).

Server-side assertions for the cheap-to-check items: footer copy, public-nav
contents, favicon link, focus/reduced-motion CSS rules, and the colorblind-safe
significance-matrix legend. Viewer affordances (loading/hint/error overlays) are
browser-only and verified via the Playwright harness, not here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_footer_drops_mvp_label():
    html = client.get("/").text
    assert "· MVP" not in html
    footer = html.split("<footer")[1]
    assert "MVP" not in footer


def test_admin_not_in_public_nav():
    html = client.get("/").text
    # v2 sidebar shell: the public nav is <nav class="b3d-navgroups">; admin must not be linked
    nav = html.split('<nav class="b3d-navgroups">')[1].split("</nav>")[0]
    assert ">Admin<" not in nav
    assert 'href="/admin"' not in nav
    # the admin route stays reachable by direct URL — but only with the admin token
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", params={"token": "test-token"}).status_code == 200


def test_favicon_link_present_and_served():
    html = client.get("/").text
    assert 'rel="icon"' in html
    assert client.get("/static/favicon.svg").status_code == 200


def test_style_has_focus_and_reduced_motion_rules():
    css = client.get("/static/style.css").text
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    # muted bumped off the old marginal value
    assert "--muted: #8b98a9" not in css


def test_significance_matrix_has_colorblind_legend():
    # cast a handful of decisive votes so a matrix renders
    import random

    random.seed(11)
    for i in range(12):
        nxt = client.get("/api/next?criterion=overall&category=all").json()
        client.post(
            "/api/vote?criterion=overall&category=all",
            json={"comparison_id": nxt["comparison_id"], "winner": "a" if i % 2 else "b"},
        )
    resp = client.post("/admin/recompute", data={"token": "test-token"})
    assert resp.status_code == 200, f"recompute failed: {resp.status_code}"
    html = client.get("/significance?criterion=overall&category=all").text
    # precondition: the matrix actually rendered (else the legend asserts pass/fail vacuously)
    assert '<table class="matrix">' in html, "matrix did not render — not enough votes"
    assert "matrix-legend" in html  # legend block rendered
    assert "row clearly ahead" in html  # legend explains the scale in words
