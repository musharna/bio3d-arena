"""Release-gate fixes from the 2026-07-27 pre-release audit.

Three findings, all measured on a public-posture boot:

* `PUBLIC_BASE_URL` still defaulted to `http://127.0.0.1:8000`, so a public deploy that forgot
  the env var emitted `og:url`/`og:image` pointing at localhost — every shared link previewed
  broken. The cookie half of this was fixed in #92 by keying on the deploy type; the URL half
  had no guard at all, and unlike a wrong cookie flag it is invisible until someone pastes a
  link somewhere public.
* There was no `rel="canonical"` at all.
* `/robots.txt` and `/sitemap.xml` both 404'd — a public launch with nothing to tell a crawler.

The guard follows the admin-token precedent: a PUBLIC deploy fails loudly at import rather
than shipping a silently-wrong default. Local and internal instances keep the convenience
default, because breaking every dev run buys nothing.
"""

from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_config():
    """Reload config from the real environment AFTER each test.

    These tests reload module-level config under a patched environment. Cleaning up inside the
    test is not enough — monkeypatch has not restored the real env yet at that point, so the
    reload captures the patched values and every later test in the session inherits them. (That
    is exactly what happened: an admin token from one of these tests leaked forward and 401'd
    `/admin/recompute` in test_research.) Reloading on teardown, after monkeypatch has undone
    its own changes, is what test_deploy_hardening already does for the same reason.
    """
    yield
    importlib.reload(config)


# --- the guard ----------------------------------------------------------------------


def test_public_deploy_refuses_the_localhost_base_url(monkeypatch):
    """A public deploy with BIO3D_PUBLIC_BASE_URL unset must not boot: every share card and
    canonical URL it emits would point at 127.0.0.1."""
    monkeypatch.setenv("BIO3D_RECON_SCORER_URL", "")  # <- public posture
    monkeypatch.setenv("BIO3D_ADMIN_TOKEN", "a-real-secret")
    monkeypatch.delenv("BIO3D_PUBLIC_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="BIO3D_PUBLIC_BASE_URL"):
        importlib.reload(config)


def test_public_deploy_accepts_a_real_base_url(monkeypatch):
    """Positive control — the guard must not block a correctly configured public deploy."""
    monkeypatch.setenv("BIO3D_RECON_SCORER_URL", "")
    monkeypatch.setenv("BIO3D_ADMIN_TOKEN", "a-real-secret")
    monkeypatch.setenv("BIO3D_PUBLIC_BASE_URL", "https://arena.example")
    importlib.reload(config)
    assert config.PUBLIC_BASE_URL == "https://arena.example"


def test_local_instance_keeps_the_localhost_default(monkeypatch):
    """The guard is scoped to PUBLIC deploys. A local/internal run (scorer URL set) must still
    default to localhost — otherwise every dev run and every test needs a new env var."""
    monkeypatch.setenv("BIO3D_RECON_SCORER_URL", "http://127.0.0.1:8800")
    monkeypatch.delenv("BIO3D_PUBLIC_BASE_URL", raising=False)
    importlib.reload(config)
    assert config.PUBLIC_BASE_URL == "http://127.0.0.1:8000"


# --- canonical ----------------------------------------------------------------------


def test_pages_declare_a_canonical_url(client):
    """Without rel=canonical, the same board reachable via query strings (?kingdom=, ?show=all)
    splits its own search ranking and shares inconsistently."""
    html = client.get("/leaderboard").text
    assert 'rel="canonical"' in html


def test_canonical_ignores_query_strings(client):
    """The canonical for a filtered view is the unfiltered page, not the filtered URL —
    otherwise every filter combination claims to be its own canonical document."""
    html = client.get("/leaderboard?kingdom=plants&show=all").text
    import re

    m = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    assert m, "no canonical link rendered"
    assert "?" not in m.group(1), f"canonical must drop the query string, got {m.group(1)}"


# --- crawler files ------------------------------------------------------------------


def test_robots_txt_is_served_and_points_at_the_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text.lower()
    assert "user-agent:" in body
    assert "sitemap:" in body, "robots.txt should advertise the sitemap"


def test_robots_keeps_crawlers_out_of_internal_and_write_surfaces(client):
    body = client.get("/robots.txt").text
    for path in ("/admin", "/api/"):
        assert f"Disallow: {path}" in body, f"robots.txt must disallow {path}"


def test_sitemap_lists_the_public_pages_and_no_internal_ones(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]
    body = r.text
    for path in ("/arena", "/leaderboard", "/models", "/dataset", "/methodology"):
        assert f"<loc>{config.PUBLIC_BASE_URL}{path}</loc>" in body, f"{path} missing from sitemap"
    # Internal surfaces must never be advertised to crawlers. /spotlight joined them on
    # 2026-07-28 (see tests/test_spotlight_is_internal.py): it 404s on the public instance now,
    # so listing it here would hand crawlers a dead URL.
    for path in (
        "/research",
        "/benchmark",
        "/significance",
        "/fidelity",
        "/procedural",
        "/spotlight",
        "/admin",
    ):
        assert f"<loc>{config.PUBLIC_BASE_URL}{path}</loc>" not in body, (
            f"{path} leaked into sitemap"
        )
