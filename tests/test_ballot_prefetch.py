"""The follow-up ballot's meshes must start downloading while the voter reads the reveal.

Measured on the live instance 2026-07-31, Fast 4G: a 4-up ballot takes **20.9 s** to become
comparable (median mesh payload 8.0 MB). That is after a 9.1x corpus recompression -- the number
was 99.6 s before it. Compression alone cannot close this; the remaining cost is simply bytes
over a slow link.

But the bytes do not have to be spent while the voter waits. `/api/vote` and `/api/kvote` already
return the FULL next ballot, which the client stashes in `pendingNext` and renders only when
"Next pair" is clicked. So from the moment a vote lands, the browser is holding the next ballot's
mesh URLs and doing nothing with them, then pays the entire download after the click.

Warming those URLs while the reveal is on screen converts that dead interval into transfer time.

Why this shape and not a lookahead fetch of `/api/next`: `_build_kwise_comparison` does
`db.add(ballot); db.commit()` on every call, so speculatively calling `/api/next` would mint a
KBallot row per prefetch and abandon it when the voter leaves -- inventing rows, and marking
quads seen for a session that never saw them. The ballot we warm here already exists; the server
built it as part of recording the vote. No extra rows, no matchmaking change.

These assert on the shipped asset rather than through a browser, matching
test_arena_client_recovery.py: the behaviour lives in static JS with no module boundary to
import, and a jsdom harness would test a reimplementation rather than the file the site serves.
The end-to-end proof that it actually warms the cache is a separate real-execution check driven
with Playwright against a throwaway database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ARENA_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "arena.js"


@pytest.fixture(scope="module")
def js() -> str:
    return ARENA_JS.read_text()


def _warm_body(js: str) -> str:
    """The source of warmBallot, so assertions about it can't accidentally match elsewhere."""
    m = re.search(r"function warmBallot\s*\([^)]*\)\s*\{(.*?)\n\}", js, re.S)
    assert m, "warmBallot() not found"
    return m.group(1)


def test_warm_ballot_exists(js):
    assert "function warmBallot" in js


def _fn_body(js: str, name: str) -> str:
    m = re.search(rf"function {name}\s*\([^)]*\)\s*\{{(.*?)\n\}}", js, re.S)
    assert m, f"{name}() not found"
    return m.group(1)


def test_it_warms_both_ballot_shapes(js):
    """A k-wise ballot carries `outputs[]`; a pairwise ballot carries `a` and `b`. Handling only
    one shape would leave the other paying full price -- and the pairwise shape is exactly what a
    task that cannot fill a quad falls back to, so it is not a rare path.

    The shape logic lives in one helper so the two ballot kinds cannot drift apart."""
    body = _fn_body(js, "ballotUrls")
    assert "outputs" in body, "k-wise ballots (data.outputs) must be warmed"
    assert re.search(r"\.a\b", body) and re.search(r"\.b\b", body), (
        "pairwise ballots (data.a / data.b) must be warmed"
    )
    assert "ballotUrls(" in _fn_body(js, "warmBallot"), (
        "warmBallot must derive urls from the shared helper"
    )


def test_it_is_invoked_where_the_next_ballot_is_stashed(js):
    """pendingNext is assigned on both the pairwise reveal and the k-wise reveal. Warming only
    one of them silently halves the benefit for whichever ballot shape was missed."""
    stashes = re.findall(r"pendingNext\s*=\s*data\.next", js)
    assert len(stashes) >= 2, f"expected both vote paths to stash a follow-up, found {len(stashes)}"
    warms = re.findall(r"warmBallot\(", js)
    # One definition + a call on each stashing path.
    assert len(warms) >= 3, f"warmBallot must be called on every stashing path, found {len(warms)}"


def test_it_never_requests_a_new_ballot(js):
    """The whole point is that the ballot ALREADY EXISTS. Calling /api/next here would mint a
    KBallot row per prefetch (`_build_kwise_comparison` commits one on every call) and abandon it
    -- inventing ballots nobody voted on and marking quads seen for a session that never saw
    them."""
    body = _warm_body(js)
    assert "/api/next" not in body, "prefetch must not create a second ballot"
    assert "/api/vote" not in body and "/api/kvote" not in body


def test_a_failed_warm_cannot_break_voting(js):
    """A prefetch is best-effort: an offline blip or a 404 on one mesh must not reject into the
    vote path and strand the voter. It must still be VISIBLE -- swallowed silently, a prefetch
    that never works would look identical to one that does."""
    body = _warm_body(js)
    assert "catch" in body, "warm failures must be caught, not left to reject"
    assert "console" in body, "a swallowed failure must still surface somewhere"


def test_it_does_not_block_the_reveal(js):
    """The reveal must paint immediately. If the call site awaited the warm, the voter would
    stare at 'Recording vote…' for the whole download -- strictly worse than today."""
    calls = list(re.finditer(r"(await\s+)?warmBallot\(", js))
    # Without this the assertion loop is empty on code that never calls warmBallot, so the test
    # would pass against the very state it exists to reject.
    assert calls, "no warmBallot call sites found"
    for m in calls:
        assert not m.group(1), "warmBallot must not be awaited on the vote path"
