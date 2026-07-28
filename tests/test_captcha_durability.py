"""A verified voter must STAY verified across a restart, a deploy, and a second machine.

Reported from the live instance while recruiting: "Cloudflare auth seems to be having occasional
issues staying authorized." Two halves compounded into an unrecoverable 403:

  * verification lived in a process-local dict (`_CAPTCHA_VERIFIED`), so every restart, deploy
    or `auto_stop_machines = "suspend"` cycle forgot every verified session — and four deploys
    went out that afternoon while people were voting;
  * the client stores ONE Turnstile token for the page's lifetime. Turnstile tokens are
    single-use and expire in ~5 minutes, so once the server forgot, the browser resent a stale
    token, got 403, and had no path back — it never re-solved.

Either half alone is survivable. Together the voter is permanently locked out mid-session, which
is exactly what "occasional" looks like from the outside.

The fix is to persist verification on the voter's own session row, so it is as durable as the
rest of that voter's state and is shared by every process. These tests pin that: they verify
once, wipe the in-memory cache to simulate a restart, and require the session to still pass.
"""

from __future__ import annotations

import uuid

import pytest

from app import config, integrity
from app.models import VoterSession


@pytest.fixture
def require_captcha(monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    monkeypatch.setattr(config, "CAPTCHA_SECRET", "test-secret")
    integrity.reset_captcha_sessions()
    yield
    integrity.reset_captcha_sessions()


def _ok(url, data):
    return {"success": True}


def _bad(url, data):
    return {"success": False}


def test_verification_survives_a_process_restart(db_session, require_captcha):
    """THE regression. Verify, wipe the in-process cache (what a deploy/restart does), and the
    same session must still be allowed to vote without re-solving."""
    sid = f"s-{uuid.uuid4().hex[:8]}"
    assert integrity.captcha_ok_for_session(db_session, sid, "fresh-token", _post=_ok) is True

    integrity.reset_captcha_sessions()  # <- simulate restart / deploy / second machine

    assert integrity.captcha_ok_for_session(db_session, sid, None, _post=_ok) is True, (
        "session lost its verification when the process memory was cleared"
    )


def test_a_stale_token_after_restart_still_passes_for_a_verified_session(
    db_session, require_captcha
):
    """The live failure mode: after a restart the browser resends its ORIGINAL token, which
    Turnstile now rejects as used/expired. A session that already verified must not be punished
    for that — otherwise it can never recover, because the widget has already fired."""
    sid = f"s-{uuid.uuid4().hex[:8]}"
    assert integrity.captcha_ok_for_session(db_session, sid, "fresh-token", _post=_ok) is True
    integrity.reset_captcha_sessions()
    assert integrity.captcha_ok_for_session(db_session, sid, "STALE", _post=_bad) is True


def test_verification_is_recorded_on_the_session_row(db_session, require_captcha):
    sid = f"s-{uuid.uuid4().hex[:8]}"
    integrity.captcha_ok_for_session(db_session, sid, "t", _post=_ok)
    row = db_session.get(VoterSession, sid)
    assert row is not None and row.captcha_verified is True


def test_an_unverified_session_with_no_token_is_refused(db_session, require_captcha):
    """Positive control on the gate: durability must not become 'everyone passes'."""
    sid = f"s-{uuid.uuid4().hex[:8]}"
    assert integrity.captcha_ok_for_session(db_session, sid, None, _post=_ok) is False
    row = db_session.get(VoterSession, sid)
    assert row is None or row.captcha_verified is False


def test_a_rejected_token_leaves_the_session_unverified(db_session, require_captcha):
    sid = f"s-{uuid.uuid4().hex[:8]}"
    assert integrity.captcha_ok_for_session(db_session, sid, "bad", _post=_bad) is False
    integrity.reset_captcha_sessions()
    assert integrity.captcha_ok_for_session(db_session, sid, None, _post=_ok) is False


def test_disabled_captcha_is_a_no_op(db_session, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", False)
    sid = f"s-{uuid.uuid4().hex[:8]}"
    assert integrity.captcha_ok_for_session(db_session, sid, None, _post=_bad) is True
