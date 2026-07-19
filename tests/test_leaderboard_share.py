"""The leaderboard is the most linkable page on the site, so it carries the same growth loop the
per-model pages have: a share control (copy-link + X-intent) and a branded Open Graph card that
unfurls when the link is posted. The card is kingdom-scoped and states NO cross-method ranking —
every board ranks one paradigm (disconnected match pools), so a single "top model" would be a
claim the ranking math does not back.
"""

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app


def setup_module(_m):
    init_db()


client = TestClient(app)


def test_leaderboard_og_card_renders_png():
    r = client.get("/og/leaderboard.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic


def test_leaderboard_og_card_accepts_scope():
    # `scope`, not `kingdom`: the kingdom rides under a distinct query param so fetching a
    # kingdom's card never flips the viewer's own board scope (the middleware reads `?kingdom=`).
    for scope in ("all", "plants", "fungi", "animals"):
        r = client.get(f"/og/leaderboard.png?scope={scope}")
        assert r.status_code == 200, scope
        assert r.headers["content-type"] == "image/png"


def test_leaderboard_og_fetch_does_not_flip_viewer_kingdom():
    """Fetching a kingdom-scoped card must not carry into a later page request. The OG image URL
    uses `?scope=`, not `?kingdom=`, precisely so the kingdom middleware's cookie is never set by
    an unfurl — the regression a shared cookie jar exposed."""
    c = TestClient(app)
    c.get("/og/leaderboard.png?scope=plants")
    r = c.get("/leaderboard")
    assert "b3d-share" in r.text, "the OG fetch must not change the viewer's board scope"


def test_leaderboard_page_has_share_control_and_og_card():
    r = client.get("/leaderboard")
    assert r.status_code == 200
    assert "b3d-share" in r.text, "leaderboard should carry the share control"
    assert "/og/leaderboard.png" in r.text, "og:image should point at the leaderboard card"
