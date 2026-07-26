"""A new comparison must never inherit the previous one's post-vote reveal.

Live-observed bug (Playwright, 2026-07-25): changing the category/criterion select while a
reveal was on screen served a brand-new, never-voted Hericium pair that still wore the
PREVIOUS pair's model-name pills ("TRELLIS via fal" / "Pixal3D"), its rank chips
("2nd"/"1st"), and the gold winner border — with the vote bar still collapsed behind
"Next pair →". Voters were shown wrong model identities on an unrelated pair.

Root cause: reveal teardown (clearReveal) was bound to the "Next pair" BUTTON CLICK rather
than to the act of rendering a comparison, so every other route to a new comparison
inherited the stale decorations. These tests pin the teardown to the render path, which is
the single funnel every comparison passes through — so a future new route cannot re-trip it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _fn_body(src: str, signature: str) -> str:
    """Return the brace-balanced body of the function introduced by ``signature``."""
    start = src.index(signature)
    open_brace = src.index("{", start)
    depth, i = 0, open_brace
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {signature!r}")


def test_render_clears_stale_reveal_before_showing_a_new_comparison():
    """render() is the single funnel for every new comparison (pairwise, K-wise, and the
    scoped-mode `done` payload), reached from loadNext(), vote(), submitKvote() and the
    Next-pair handler alike. Clearing there — not at one call site — removes the bug class."""
    ajs = client.get("/static/arena.js").text
    assert "clearReveal()" in _fn_body(ajs, "function render(")


def test_empty_state_also_clears_stale_reveal():
    """The /api/next 404 empty state replaces the stage without going through render(), so a
    filter change into an empty category would otherwise strand the reveal on the empty view."""
    ajs = client.get("/static/arena.js").text
    assert "clearReveal()" in _fn_body(ajs, "function renderNoComparisons(")


def test_clear_reveal_restores_the_vote_bar():
    """The stale reveal also left the vote bar collapsed (disableVoteBar(true) from showReveal),
    so clearing must re-enable voting or the new pair is unvotable."""
    ajs = client.get("/static/arena.js").text
    assert "disableVoteBar(false)" in _fn_body(ajs, "function clearReveal(")
