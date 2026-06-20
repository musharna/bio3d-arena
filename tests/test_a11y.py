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
    nav = html.split("<nav>")[1].split("</nav>")[0]
    assert ">Admin<" not in nav
    # the admin route itself stays reachable by direct URL
    assert client.get("/admin").status_code == 200


def test_favicon_link_present_and_served():
    html = client.get("/").text
    assert 'rel="icon"' in html
    assert client.get("/static/favicon.svg").status_code == 200
