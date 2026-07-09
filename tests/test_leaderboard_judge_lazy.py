"""The VLM-judge board is collapsed by default and expensive to fit (200-bootstrap BT over the
kingdom judge graph, ~11s for plants on a cold cache). It must NOT be computed on the main
/leaderboard render — the page ships a lazy container that fetches /leaderboard/judge only when
the <details> is expanded, so the main page never blocks on judge BT."""

from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_main_leaderboard_has_lazy_judge_container_not_eager_board():
    html = client.get("/leaderboard").text
    # The lazy container carries the fetch URL; the judge board is NOT rendered inline.
    assert 'data-judge-url="/leaderboard/judge' in html


def test_judge_fragment_endpoint_exists():
    r = client.get("/leaderboard/judge")
    assert r.status_code == 200
    # Fragment only (no full-page shell) — must not carry the site chrome.
    assert "<html" not in r.text.lower()
