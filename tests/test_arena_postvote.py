"""After a vote, the vote controls must stop reading as clickable: the 2-up vote bar is HIDDEN
(Next-pair replaces it) and any still-visible vote button (the K-wise "Pick this one") carries a
clear :disabled look. Served-asset wiring guard, mirroring tests/test_flag_client.py."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_votebar_is_hidden_on_reveal_not_just_disabled():
    ajs = client.get("/static/arena.js").text
    # disableVoteBar (called by showReveal) collapses the whole bar so "Next pair →" replaces it.
    assert 'disabled ? "none"' in ajs
    assert 'id="next-pair-btn"' in client.get("/arena").text or "next-pair-btn" in ajs


def test_disabled_vote_button_has_inert_style():
    css = client.get("/static/style.css").text
    assert ".vote-btn:disabled" in css
