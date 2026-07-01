"""Hugging Face OAuth2/OIDC helpers. Pure + injectable HTTP so tests never hit the network."""

from __future__ import annotations

import json as _json
import secrets
import urllib.parse as _url
import urllib.request as _req

from . import config

AUTHORIZE_URL = "https://huggingface.co/oauth/authorize"
TOKEN_URL = "https://huggingface.co/oauth/token"
USERINFO_URL = "https://huggingface.co/oauth/userinfo"
SCOPE = "openid profile"


class AuthError(RuntimeError):
    """OAuth exchange/userinfo failure (network, provider error, or malformed response)."""


def _login_enabled() -> bool:
    return bool(config.HF_CLIENT_ID and config.HF_CLIENT_SECRET)


# module-level convenience mirror; routes check auth.LOGIN_ENABLED at call time via the function
LOGIN_ENABLED = _login_enabled()


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(state: str, redirect_uri: str) -> str:
    q = _url.urlencode(
        {
            "client_id": config.HF_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{q}"


def _post_form(url: str, data: dict) -> dict:
    body = _url.urlencode(data).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    with _req.urlopen(_req.Request(url, data=body, headers=headers), timeout=10) as r:
        return _json.loads(r.read().decode())


def _get_json(url: str, access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    with _req.urlopen(_req.Request(url, headers=headers), timeout=10) as r:
        return _json.loads(r.read().decode())


def exchange_code(code: str, redirect_uri: str, *, _post=_post_form) -> str:
    try:
        res = _post(
            TOKEN_URL,
            {
                "client_id": config.HF_CLIENT_ID,
                "client_secret": config.HF_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    except Exception as e:  # noqa: BLE001
        raise AuthError(f"token exchange failed: {e}") from e
    if not isinstance(res, dict) or "access_token" not in res:
        raise AuthError(f"token endpoint returned no access_token: {res!r}")
    return res["access_token"]


def fetch_userinfo(access_token: str, *, _get=_get_json) -> dict:
    try:
        res = _get(USERINFO_URL, access_token)
    except Exception as e:  # noqa: BLE001
        raise AuthError(f"userinfo failed: {e}") from e
    if not isinstance(res, dict) or not res.get("sub"):
        raise AuthError(f"userinfo missing sub: {res!r}")
    return {
        "hf_id": str(res["sub"]),
        "username": res.get("preferred_username") or res.get("name") or "",
    }
