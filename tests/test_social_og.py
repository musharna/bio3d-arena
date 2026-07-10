"""Every page emits Open Graph + Twitter share meta so a pasted link previews with a title,
description and the branded card image (instead of a bare URL). URLs are absolute (og requires
it); key pages carry their own description."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app import config, main
from app.main import app

client = TestClient(app)


def _meta(html: str, prop: str, attr: str = "property") -> str:
    # Whitespace-tolerant: the HTML formatter may wrap a <meta> across lines (valid HTML that OG
    # scrapers parse fine), so allow newlines/spaces between the tag name and its attributes.
    m = re.search(rf'<meta\s+{attr}="{re.escape(prop)}"\s+content="([^"]*)"', html)
    return m.group(1) if m else ""


def test_abs_url_normalizes_leading_slash():
    assert main._abs_url("/arena") == config.PUBLIC_BASE_URL + "/arena"
    assert main._abs_url("arena") == config.PUBLIC_BASE_URL + "/arena"


def test_home_emits_og_and_twitter_meta():
    html = client.get("/").text
    assert _meta(html, "og:title")
    assert _meta(html, "og:type") == "website"
    assert _meta(html, "og:site_name")
    assert _meta(html, "twitter:card", attr="name") == "summary_large_image"
    assert _meta(html, "description", attr="name")


def test_og_url_and_image_are_absolute():
    html = client.get("/").text
    assert _meta(html, "og:url").startswith(config.PUBLIC_BASE_URL)
    assert _meta(html, "og:image").startswith(config.PUBLIC_BASE_URL)
    assert _meta(html, "og:image").endswith(".png")


def test_default_og_image_is_served():
    r = client.get("/static/og-default.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


def test_key_pages_have_distinct_descriptions():
    home = _meta(client.get("/").text, "description", attr="name")
    lb = _meta(client.get("/leaderboard").text, "description", attr="name")
    assert home and lb and home != lb
