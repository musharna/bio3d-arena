"""The VLM-judge board is collapsed by default and expensive to fit (200-bootstrap BT over the
kingdom judge graph, ~11s for plants on a cold cache). It must NOT be computed on the main
/leaderboard render — the page ships a lazy container that fetches /leaderboard/judge only when
the <details> is expanded, so the main page never blocks on judge BT.

/leaderboard/judge now serves TWO consumers: a browser navigating to it gets the standalone
AI-judge PAGE (tests/test_judge_delineation.py), while this lazy container fetches the BARE
FRAGMENT via the `fragment=1` on data-judge-url (leaderboard.js assigns the response straight to
.innerHTML, so a full page would nest <html> inside a <div>). This module owns the lazy half."""

import re

from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def _lazy_judge_url(html: str) -> str:
    m = re.search(r'data-judge-url="([^"]+)"', html)
    assert m, "the lazy judge container is missing from the leaderboard render"
    return m.group(1).replace("&amp;", "&")


def test_main_leaderboard_has_lazy_judge_container_not_eager_board():
    html = client.get("/leaderboard").text
    # The lazy container carries the fetch URL; the judge board is NOT rendered inline.
    assert 'data-judge-url="/leaderboard/judge' in html


def test_judge_fragment_endpoint_exists():
    """The URL leaderboard.js actually fetches (read off the container, not hard-coded) must
    still answer with a bare fragment — no full-page shell to nest inside the <details>."""
    url = _lazy_judge_url(client.get("/leaderboard").text)
    assert "fragment=1" in url
    r = client.get(url)
    assert r.status_code == 200
    assert "<html" not in r.text.lower()
