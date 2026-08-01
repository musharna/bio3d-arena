"""A board whose models are off the vote roster must say so.

Scoping the arena to the commercial-model paradigms (config.ARENA_VOTE_PARADIGMS) means the
procedural_llm and agentic boards stop accruing human votes. Their rows still render, still
carry whatever BT score the votes so far produced, and still read "provisional" — which,
unexplained, looks like a board nobody has got round to voting on rather than one deliberately
outside the current human pool. The AI-judge board DOES still rank them, and that is the thing
a reader needs pointed at.

This is the same honesty posture as the rest of the site: say what the number is and isn't.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _board(client, paradigm):
    """One modality's board. The query param is `paradigm=` — `modality=` is the JUDGE board's
    param, and passing it here silently renders the cross-modality HUB instead, which carries
    no per-board note at all and would make every assertion below vacuous."""
    r = client.get(f"/leaderboard?paradigm={paradigm}")
    assert r.status_code == 200, r.status_code
    assert "lb-what-measures" in r.text, "rendered the hub, not a single-modality board"
    return r.text


def test_an_off_roster_board_explains_that_human_voting_is_scoped(client):
    html = _board(client, "procedural_llm")
    assert "off-roster-note" in html, "off-roster board carries no explanation"


def test_an_on_roster_board_carries_no_such_note(client):
    """Positive control. Without it, a note rendered unconditionally would satisfy the test
    above while telling every reader their board is paused."""
    html = _board(client, "image_recon")
    assert "off-roster-note" not in html


def test_the_note_points_at_the_judge_board(client):
    """The useful half of the message: these models ARE still ranked, just not by humans."""
    html = _board(client, "agentic")
    assert "off-roster-note" in html
    assert "/leaderboard/judge" in html


def test_no_board_is_off_roster_when_the_roster_is_open(client, monkeypatch):
    """With scoping disabled every paradigm is votable, so the note must disappear everywhere —
    it is keyed off the live config, not hard-coded to two paradigm names."""
    monkeypatch.setattr(config, "ARENA_VOTE_PARADIGMS", frozenset())
    assert "off-roster-note" not in _board(client, "procedural_llm")
