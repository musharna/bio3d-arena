"""The sidebar is a product surface, not an index of everything we built.

Before this, the sidebar carried 13 links across 5 groups — 10 of them public. Six were
research instruments (Benchmark, Significance, Difficulty) or corpus detail (Coverage,
Tasks) that nothing else in the product linked to, and Submit is an action that already
lives in the footer. Meanwhile /fidelity and /procedural were live pages with NO nav entry
at all, reachable only by typing the URL.

The IA here: the sidebar carries the product loop (vote -> ranking -> model) plus the two
browsable data surfaces, and every research instrument sits behind ONE internal-only
"Research" hub. Nothing is deleted — the demoted routes keep serving, they just stop
competing for a top-level slot.
"""

import re

import pytest
from starlette.testclient import TestClient

from app import config
from app.main import app

# Sidebar links only. The compact top nav uses `b3d-topnav-link` and the footer uses
# `b3d-footer-chip`, so this class isolates the sidebar without matching either.
SIDEBAR_LINK_CLASS = "b3d-nav-link"

PUBLIC_SIDEBAR = [
    "/",
    "/arena",
    "/leaderboard",
    "/models",
    "/dataset",
    "/spotlight",
    "/methodology",
]

# Demoted out of the sidebar. Each keeps its route; only the top-level slot goes away.
DEMOTED = ["/coverage", "/tasks", "/submit"]

# Research instruments, now reachable only through the /research hub.
RESEARCH_BOARDS = ["/benchmark", "/significance", "/difficulty", "/fidelity", "/procedural"]


@pytest.fixture
def client():
    return TestClient(app)


def sidebar(html: str) -> str:
    """Isolate the sidebar nav block so footer/topnav links can't satisfy an assertion.

    Without this, `'href="/submit"' not in html` would fail purely because the footer
    still (correctly) offers "Submit a model" — the test would be measuring the wrong nav.
    """
    m = re.search(r'<nav class="b3d-navgroups">(.*?)</nav>', html, re.S)
    assert m, "sidebar nav block not found — template structure changed"
    return m.group(1)


def test_public_sidebar_carries_exactly_the_product_loop(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    nav = sidebar(client.get("/").text)
    assert nav.count(SIDEBAR_LINK_CLASS) == len(PUBLIC_SIDEBAR)
    for href in PUBLIC_SIDEBAR:
        assert f'href="{href}"' in nav, f"public sidebar must keep {href}"


def test_internal_sidebar_adds_only_the_research_hub(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    nav = sidebar(client.get("/").text)
    assert nav.count(SIDEBAR_LINK_CLASS) == len(PUBLIC_SIDEBAR) + 1
    assert 'href="/research"' in nav


def test_research_hub_absent_from_public_sidebar(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    assert 'href="/research"' not in sidebar(client.get("/").text)


@pytest.mark.parametrize("internal", [False, True])
def test_demoted_and_research_pages_leave_the_sidebar(client, monkeypatch, internal):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", internal, raising=False)
    nav = sidebar(client.get("/").text)
    for href in DEMOTED + RESEARCH_BOARDS:
        assert f'href="{href}"' not in nav, f"{href} must not hold a sidebar slot"


def test_research_hub_is_internal_only(client, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", False, raising=False)
    assert client.get("/research").status_code == 404
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    assert client.get("/research").status_code == 200


def test_research_hub_links_every_board_including_the_two_that_were_unreachable(
    client, monkeypatch
):
    # /fidelity and /procedural had no nav entry before — the hub is what makes them
    # findable. A hub that only relisted the three already-navigable boards would leave
    # the orphans orphaned, so assert on all five.
    monkeypatch.setattr(config, "INTERNAL_PAGES_ENABLED", True, raising=False)
    html = client.get("/research").text
    for href in RESEARCH_BOARDS:
        assert f'href="{href}"' in html, f"research hub must link {href}"


def main_content(html: str) -> str:
    """Isolate the page body. Scoping matters here: while these links were still in the
    sidebar, a whole-page search passed regardless of what /dataset itself contained."""
    m = re.search(r'<main class="b3d-main">(.*?)</main>', html, re.S)
    assert m, "main content block not found — template structure changed"
    return m.group(1)


def test_dataset_adopts_coverage_and_tasks(client):
    # Demoting these two is only sound if /dataset picks them up — otherwise they become
    # as unreachable as /fidelity was.
    body = main_content(client.get("/dataset").text)
    assert 'href="/coverage"' in body
    assert 'href="/tasks"' in body


def test_demoted_routes_still_serve(client):
    # This change is IA, not deletion. Bookmarks and inbound links must survive.
    for path in DEMOTED:
        assert client.get(path).status_code == 200, f"{path} must still serve"


def test_footer_still_offers_submit(client):
    assert 'href="/submit"' in client.get("/").text
