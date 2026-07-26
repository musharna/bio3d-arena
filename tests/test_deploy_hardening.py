"""Deploy-time safety: the public instance must not be able to boot insecurely.

Two defects this pins, both found by the 2026-07-26 pre-launch audit and both of the
same shape — a *silent* fallback that is safe locally and unsafe in production:

1. ``ADMIN_TOKEN`` fell back to the literal ``"changeme-admin-token"``. A public deploy
   that forgot ``BIO3D_ADMIN_TOKEN`` served ``/admin`` (create/modify generators, tasks,
   outputs; trigger recomputes) behind a token published in the source tree. Verified
   live before the fix: ``GET /admin?token=changeme-admin-token`` -> 200.

2. ``COOKIE_SECURE`` was *derived* from ``PUBLIC_BASE_URL.startswith("https://")``, so
   forgetting ``BIO3D_PUBLIC_BASE_URL`` silently dropped the Secure flag from session
   cookies. Cookie security was a side effect of a URL string.

Both now key off the deploy signal the codebase ALREADY uses to tell public from
internal — an empty ``BIO3D_RECON_SCORER_URL`` (see ``SCORING_ENABLED`` /
``INTERNAL_PAGES_ENABLED``) — so neither needs a new knob a deployer could forget.
"""

from __future__ import annotations

import importlib

import pytest

from app import config

PUBLIC_ENV = {"BIO3D_RECON_SCORER_URL": ""}  # empty scorer url == the public deploy
INTERNAL_ENV = {"BIO3D_RECON_SCORER_URL": "http://127.0.0.1:8800"}


@pytest.fixture(autouse=True)
def _restore_config():
    """Reload config from the real environment afterwards.

    These tests mutate module-level config by reloading it; without this the last
    reload would leak into every later test in the session.
    """
    yield
    importlib.reload(config)


def _reload(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(config)


# --- 1. admin token -------------------------------------------------------


def test_public_deploy_without_admin_token_refuses_to_boot(monkeypatch):
    """The whole point: fail loud at import, so a misconfigured deploy cannot serve."""
    monkeypatch.delenv("BIO3D_ADMIN_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as exc:
        _reload(monkeypatch, **PUBLIC_ENV)
    assert "BIO3D_ADMIN_TOKEN" in str(exc.value)


def test_public_deploy_with_explicit_admin_token_boots(monkeypatch):
    cfg = _reload(monkeypatch, **PUBLIC_ENV, BIO3D_ADMIN_TOKEN="a-real-secret")
    assert cfg.ADMIN_TOKEN == "a-real-secret"


def test_internal_deploy_keeps_working_without_an_explicit_token(monkeypatch):
    """Local/internal dev must not be broken by the guard — it is the public deploy
    that is dangerous, and an internal instance binds loopback."""
    monkeypatch.delenv("BIO3D_ADMIN_TOKEN", raising=False)
    cfg = _reload(monkeypatch, **INTERNAL_ENV)
    assert cfg.ADMIN_TOKEN  # a usable local default, no raise


def test_the_published_default_token_is_never_accepted_on_a_public_deploy(monkeypatch):
    """Setting the env var to the old literal must NOT satisfy the guard — otherwise
    copy-pasting the documented default reintroduces exactly the audited hole."""
    with pytest.raises(RuntimeError):
        _reload(monkeypatch, **PUBLIC_ENV, BIO3D_ADMIN_TOKEN="changeme-admin-token")


# --- 2. cookie Secure -----------------------------------------------------


def test_cookie_secure_is_on_for_a_public_deploy_even_without_a_base_url(monkeypatch):
    """The audited chain: forgetting BIO3D_PUBLIC_BASE_URL used to silently disable
    Secure. Cookie security must not depend on a URL string being remembered."""
    monkeypatch.delenv("BIO3D_PUBLIC_BASE_URL", raising=False)
    cfg = _reload(monkeypatch, **PUBLIC_ENV, BIO3D_ADMIN_TOKEN="s")
    assert cfg.COOKIE_SECURE is True


def test_cookie_secure_is_off_for_local_http_dev(monkeypatch):
    """Secure cookies are not sent over plain http, so local dev would break."""
    monkeypatch.delenv("BIO3D_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("BIO3D_COOKIE_SECURE", raising=False)
    cfg = _reload(monkeypatch, **INTERNAL_ENV)
    assert cfg.COOKIE_SECURE is False


def test_explicit_cookie_secure_override_still_wins(monkeypatch):
    cfg = _reload(monkeypatch, **PUBLIC_ENV, BIO3D_ADMIN_TOKEN="s", BIO3D_COOKIE_SECURE="false")
    assert cfg.COOKIE_SECURE is False


def test_https_base_url_still_turns_secure_on_internally(monkeypatch):
    cfg = _reload(monkeypatch, **INTERNAL_ENV, BIO3D_PUBLIC_BASE_URL="https://arena.example")
    assert cfg.COOKIE_SECURE is True
