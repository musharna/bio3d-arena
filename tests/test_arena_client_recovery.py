"""The arena client must be able to recover from a lapsed captcha, and must not let a drag
paint selection highlight over one of the two candidates.

Both were reported from the live instance during recruiting.

A Turnstile token is single-use and expires in ~5 minutes, and the widget fires its callback
once. So when the server stopped recognising a session, the browser resent the same spent token,
got another 403, and had no path back — the voter was locked out for the rest of the visit. The
server half is fixed by persisting verification (test_captcha_durability.py); this is the client
half: on a 403, ask the widget for a FRESH token and retry once.

These assert on the shipped asset rather than through a browser: the behaviour lives in static
JS with no module boundary to import, and a jsdom harness would test a reimplementation rather
than the file the site serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ARENA_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "arena.js"
STYLE_CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"


@pytest.fixture(scope="module")
def js() -> str:
    return ARENA_JS.read_text()


@pytest.fixture(scope="module")
def css() -> str:
    return STYLE_CSS.read_text()


def test_client_can_request_a_fresh_captcha_token(js):
    assert "function refreshCaptchaToken" in js
    assert re.search(r"api\.reset\(\)", js), "must call the provider's reset() to re-challenge"


def test_refresh_supports_both_providers(js):
    """The widget template renders hcaptcha OR turnstile; a recovery path that only knows one
    silently fails on the other."""
    assert "window.hcaptcha" in js and "window.turnstile" in js


def test_a_403_triggers_a_refresh_and_one_retry(js):
    assert re.search(r"res\.status\s*===\s*403", js), "403 must be handled specifically"
    assert re.search(r"await\s+refreshCaptchaToken\(\)", js)
    # Exactly one retry: the retry must be guarded by having obtained a fresh token, not looped.
    assert re.search(r"if\s*\(fresh\)\s*res\s*=\s*await\s+post\(\)", js), (
        "retry must happen once, and only with a genuinely fresh token"
    )


def test_refresh_cannot_hang_the_vote_path(js):
    """If the challenge never answers, the promise must settle anyway — otherwise the vote
    button stays stuck on 'Re-checking…' forever with `busy` still true."""
    assert "timeoutMs" in js and re.search(r"setTimeout\(\(\)\s*=>\s*finish\(\"\"\)", js)


def test_a_persistent_403_tells_the_voter_what_to_do(js):
    """A bare 'vote not recorded' leaves the voter with no action. If the retry also fails the
    widget needs ticking, so say that."""
    assert "verification box" in js


def test_the_comparison_suppresses_text_selection(css):
    """Dragging to orbit also selects text; on Safari/macOS the highlight landed on top of the
    models being judged. A highlight over one of two candidates is a presentation difference
    between them — precisely what a blind comparison must not introduce."""
    m = re.search(r"\.pair\s*\{(.*?)\}", css, re.S)
    assert m, ".pair rule not found"
    block = m.group(1)
    assert "user-select: none" in block
    assert "-webkit-user-select: none" in block, "Safari needs the prefixed property"
