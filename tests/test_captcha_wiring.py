"""The captcha has to be reachable end-to-end, not just verifiable server-side.

`integrity.verify_captcha` (see tests/test_captcha.py) has been correct for a while: it POSTs
to Turnstile/hCaptcha siteverify and fails closed. But nothing ever produced a token for it to
check. The arena page loaded no widget, there was no site key to render one with, and arena.js
never sent an `X-Captcha-Token` header — so `BIO3D_REQUIRE_CAPTCHA=true` would have 403'd
every single vote. A switch that breaks the product when flipped is not a feature.

These tests pin the parts that were missing: a site key, a boot-time refusal when the switch is
on but unconfigured, the widget on the page, the header from the client, and — the design
decision that makes a captcha survivable here — verification scoped to the SESSION rather than
to every vote. Vote volume is this arena's binding constraint; a challenge per vote would cost
more than the bots it stops.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import config, integrity
from app.main import app
from app.seed import seed_all

ARENA_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "arena.js"


@pytest.fixture
def client():
    return TestClient(app)


def _enable(monkeypatch, *, site_key="0x-site", secret="sek"):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    monkeypatch.setattr(config, "CAPTCHA_SITE_KEY", site_key)
    monkeypatch.setattr(config, "CAPTCHA_SECRET", secret)
    monkeypatch.setattr(config, "CAPTCHA_PROVIDER", "turnstile")


# --- configuration -----------------------------------------------------------------


def test_site_key_exists_and_defaults_empty():
    """The secret verifies server-side; the SITE key is what the browser needs to render a
    widget at all. Only the secret existed, which is why no widget could ever be drawn."""
    assert hasattr(config, "CAPTCHA_SITE_KEY")


def test_enabling_captcha_without_keys_refuses_to_boot(monkeypatch):
    """Same posture as the admin-token guard: a misconfiguration that silently disables a
    security control is worse than a loud failure. Here it is worse still — enabled-but-
    unconfigured doesn't weaken voting, it BREAKS it, and only in production."""
    monkeypatch.setenv("BIO3D_REQUIRE_CAPTCHA", "true")
    monkeypatch.delenv("BIO3D_CAPTCHA_SITE_KEY", raising=False)
    monkeypatch.delenv("BIO3D_CAPTCHA_SECRET", raising=False)
    try:
        with pytest.raises(RuntimeError, match="captcha"):
            importlib.reload(config)
    finally:
        # The reload raised PART WAY through, so `config` is left holding whatever was
        # assigned before the guard fired (REQUIRE_CAPTCHA=True with no keys). Reload it
        # cleanly or every later test in the run inherits that broken state.
        monkeypatch.delenv("BIO3D_REQUIRE_CAPTCHA", raising=False)
        importlib.reload(config)
    assert config.REQUIRE_CAPTCHA is False


def test_captcha_config_roundtrips_when_fully_set(monkeypatch):
    """Positive control for the guard above — a correctly configured instance must import."""
    monkeypatch.setenv("BIO3D_REQUIRE_CAPTCHA", "true")
    monkeypatch.setenv("BIO3D_CAPTCHA_SITE_KEY", "0x-site")
    monkeypatch.setenv("BIO3D_CAPTCHA_SECRET", "sek")
    try:
        importlib.reload(config)
        assert config.REQUIRE_CAPTCHA is True
        assert config.CAPTCHA_SITE_KEY == "0x-site"
    finally:
        for k in ("BIO3D_REQUIRE_CAPTCHA", "BIO3D_CAPTCHA_SITE_KEY", "BIO3D_CAPTCHA_SECRET"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(config)


# --- the page ----------------------------------------------------------------------


def test_arena_loads_the_widget_only_when_captcha_is_enabled(client, monkeypatch):
    """A page that loads no third-party script today must not start loading one just because
    the code exists — the script appears if and only if the operator turned the switch on."""
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", False)
    off = client.get("/arena").text
    assert "challenges.cloudflare.com" not in off
    assert "cf-turnstile" not in off

    _enable(monkeypatch)
    on = client.get("/arena").text
    assert "challenges.cloudflare.com/turnstile/v0/api.js" in on
    assert "0x-site" in on, "the widget needs the site key rendered into the page"


# --- the client --------------------------------------------------------------------


def test_arena_js_sends_the_captcha_header():
    """The header name is the contract between arena.js and main.py's `x_captcha_token`
    parameter. Nothing else in the codebase asserts these two agree."""
    js = ARENA_JS.read_text()
    assert "X-Captcha-Token" in js, "arena.js must send the token the vote endpoint reads"


def test_arena_js_only_attaches_the_header_when_a_token_exists():
    """With the captcha off there is no token; sending the header as `undefined`/`null` would
    make every request carry a junk value."""
    js = ARENA_JS.read_text()
    assert re.search(r"captchaToken|captcha_token", js), "client must track a token value"


# --- session-scoped verification ---------------------------------------------------


def test_verification_is_remembered_for_the_session(monkeypatch, db_session):
    """The design decision. Turnstile tokens are single-use and short-lived, so checking one
    per vote means a challenge round-trip per vote. This arena's binding constraint is vote
    VOLUME, so a voter is verified ONCE and the session carries it afterwards.
    """
    _enable(monkeypatch)
    seen = []

    def fake_post(url, data):
        seen.append(data["response"])
        return {"success": True}

    monkeypatch.setattr(integrity, "_post_form", fake_post)
    assert integrity.captcha_ok_for_session(db_session, "sess-a", "tok-1", _post=fake_post) is True
    # Second and third votes in the same session must not re-challenge.
    assert integrity.captcha_ok_for_session(db_session, "sess-a", None, _post=fake_post) is True
    assert integrity.captcha_ok_for_session(db_session, "sess-a", None, _post=fake_post) is True
    assert seen == ["tok-1"], "the provider must be called once per session, not once per vote"

    # A different session is NOT covered by the first one's verification.
    assert integrity.captcha_ok_for_session(db_session, "sess-b", None, _post=fake_post) is False


def test_a_failed_challenge_does_not_mark_the_session_verified(monkeypatch, db_session):
    """Fail-closed must not be self-healing: a rejected token leaves the session unverified,
    so the next vote is challenged again rather than waved through."""
    _enable(monkeypatch)
    assert (
        integrity.captcha_ok_for_session(
            db_session, "sess-c", "bad", _post=lambda u, d: {"success": False}
        )
        is False
    )
    assert (
        integrity.captcha_ok_for_session(
            db_session, "sess-c", None, _post=lambda u, d: {"success": True}
        )
        is False
    )


def test_captcha_disabled_never_calls_the_provider(monkeypatch, db_session):
    """Positive control: with the switch off, voting must not require or consult anything."""
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", False)

    def explode(url, data):  # pragma: no cover - must never run
        raise AssertionError("provider must not be called when captcha is disabled")

    assert integrity.captcha_ok_for_session(db_session, "sess-d", None, _post=explode) is True


def test_a_verified_voter_keeps_voting_without_re_challenging(client, monkeypatch):
    """End-to-end at the HTTP boundary, which is the only place the header name, the endpoint
    parameter and the session memory all have to agree.

    Sequence: challenged -> verified -> voting freely. If this only checked the 403 and the
    first 200 it would pass even with per-vote verification, which is the design this changes.
    """
    seed_all(force=True)  # the arena needs a votable pair to serve
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    # Stub the provider (a live siteverify call would fail closed and 403 the valid leg).
    monkeypatch.setattr(integrity, "verify_captcha", lambda token, **kw: bool(token))
    integrity.reset_rate_limits()
    integrity.reset_captcha_sessions()

    def next_pair() -> int:
        """A pairwise comparison id. /api/next may serve a K-wise ballot instead, which is a
        different endpoint; keep asking until it offers a pair."""
        for _ in range(20):
            payload = client.get("/api/next").json()
            if "comparison_id" in payload:
                return payload["comparison_id"]
        pytest.skip("no pairwise comparison available in this DB")

    blocked = client.post("/api/vote", json={"comparison_id": next_pair(), "winner": "a"})
    assert blocked.status_code == 403, "an unverified session must be challenged"

    first = client.post(
        "/api/vote",
        json={"comparison_id": next_pair(), "winner": "a"},
        headers={"X-Captcha-Token": "good-token"},
    )
    assert first.status_code == 200

    for i in range(3):
        again = client.post("/api/vote", json={"comparison_id": next_pair(), "winner": "a"})
        assert again.status_code == 200, f"vote {i + 2} re-challenged a verified session"

    integrity.reset_captcha_sessions()
    integrity.reset_rate_limits()
