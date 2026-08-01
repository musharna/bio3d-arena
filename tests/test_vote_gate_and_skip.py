"""Two live reports from recruiting, one underlying problem: voters were being asked for
judgements they could not actually make.

**Triangles while loading.** `model-viewer` renders a GLB progressively, so a half-arrived mesh
shows as a handful of loose triangles — which is exactly what a genuinely degenerate output looks
like (the corpus contains a few, and the gold decoy looks like that deliberately). A voter cannot
tell "still loading" from "this model produced garbage", so they vote on network timing. Fixed by
holding the frame blank until the mesh is complete (`reveal="manual"` + `dismissPoster()`), and by
locking the vote bar until BOTH slots settle.

**No way to say "I can't tell".** The only outlets were Tie and Both bad, which are real
judgements the Bradley-Terry fit consumes at 0.5 and 0. Routing an abstention into either injects
noise into precisely the comparisons where the signal is weakest, so Skip records nothing.

Asserted against the shipped assets: this behaviour lives in static JS/CSS/templates with no
module boundary to import, and a jsdom harness would be testing a reimplementation rather than the
files the site serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARENA_JS = ROOT / "app" / "static" / "arena.js"
VIEWER_JS = ROOT / "app" / "static" / "viewer.js"
ARENA_HTML = ROOT / "app" / "templates" / "arena.html"
STYLE_CSS = ROOT / "app" / "static" / "style.css"


@pytest.fixture(scope="module")
def arena_js() -> str:
    return ARENA_JS.read_text()


@pytest.fixture(scope="module")
def viewer_js() -> str:
    return VIEWER_JS.read_text()


# --- no partial geometry is ever shown -------------------------------------------------


def test_mesh_is_hidden_until_fully_loaded(viewer_js):
    assert 'setAttribute("reveal", "manual")' in viewer_js, (
        "without manual reveal, model-viewer paints a partly-streamed mesh as loose triangles"
    )
    assert "dismissPoster()" in viewer_js, "manual reveal must be dismissed on load"


def test_dismiss_is_tolerant_of_an_older_viewer(viewer_js):
    """dismissPoster is absent on older model-viewer builds; throwing there would leave the
    frame permanently blank — strictly worse than the bug being fixed."""
    m = re.search(r"dismissPoster\(\);\s*\}\s*catch", viewer_js)
    assert m, "dismissPoster must be guarded"


def test_both_load_and_failure_settle_the_gate(viewer_js):
    """A model that fails to load is settled too — the voter can see it failed and 'both bad' is
    a legitimate call. Emitting only on success would wedge the vote bar forever."""
    assert viewer_js.count("bio3d:viewer-settled") >= 2
    assert re.search(r"detail:\s*\{\s*ok:\s*true\s*\}", viewer_js)
    assert re.search(r"detail:\s*\{\s*ok:\s*false\s*\}", viewer_js)


# --- the vote gate ---------------------------------------------------------------------


def test_vote_bar_is_locked_until_both_slots_settle(arena_js):
    assert "function armVoteGate" in arena_js
    assert re.search(r"settledCount\s*>=\s*2", arena_js), "must wait for BOTH slots"
    assert re.search(r"armVoteGate\(\)", arena_js)


def test_the_gate_is_re_armed_for_every_new_pair(arena_js):
    """Locking once at startup would leave every subsequent pair unguarded."""
    m = re.search(r"function renderPair\(data\)\s*\{(.{0,400})", arena_js, re.S)
    assert m and "armVoteGate()" in m.group(1), "renderPair must re-lock the gate"


def test_a_stuck_viewer_cannot_strand_the_voter(arena_js):
    """Positive control on the gate: if a mesh never settles, the bar must open anyway rather
    than trapping someone on a pair they can never leave by voting."""
    assert re.search(r"setInterval\(", arena_js)
    assert re.search(r"settledCount\s*<\s*2\)\s*setVoteEnabled\(true\)", arena_js)


# --- skip is not a vote ----------------------------------------------------------------


def test_skip_control_exists_and_is_bound():
    html = ARENA_HTML.read_text()
    assert 'id="skip-btn"' in html
    js = ARENA_JS.read_text()
    assert "function skipPair" in js
    assert re.search(r"skipBtn\.addEventListener\(\"click\", skipPair\)", js)


def test_skip_records_nothing(arena_js):
    """THE point of the control. If skipPair ever posted, an abstention would enter the
    Bradley-Terry fit as a real comparison."""
    m = re.search(r"async function skipPair\(\)\s*\{(.*?)\n\}", arena_js, re.S)
    assert m, "skipPair not found"
    body = m.group(1)
    assert "fetch(" not in body and "/api/vote" not in body, "skip must not reach the vote endpoint"
    assert "loadNext()" in body


def test_skip_is_not_wired_as_a_winner_value(arena_js):
    """A `data-winner="skip"` would be picked up by the generic vote-bar binding and POSTed."""
    html = ARENA_HTML.read_text()
    assert 'data-winner="skip"' not in html


def test_skip_has_a_keyboard_shortcut(arena_js):
    assert re.search(r'e\.key === "s" \|\| e\.key === "S"', arena_js)


def test_skip_is_styled_subordinate_to_the_vote_bar():
    css = STYLE_CSS.read_text()
    assert ".skip-btn" in css and ".vote-bar.is-waiting" in css
