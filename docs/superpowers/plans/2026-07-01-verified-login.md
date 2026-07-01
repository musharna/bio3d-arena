# SP2 — HF Verified Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional Hugging Face OAuth login so voters can verify identity; verified votes form a higher-integrity leaderboard scope. Anonymous voting is unchanged; login is disabled by default until HF secrets are set.

**Architecture:** Standard OAuth2/OIDC against Hugging Face. A pure/injectable `app/auth.py` (no network in tests), a `User` model + a nullable `VoterSession.user_id` link, three `/auth/*` routes, a `verified_only` filter on the existing BT match-collection, and nav UI. Verified identity attaches to the _existing_ anonymous session cookie.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, pytest. Stdlib `urllib`, `secrets`, `hmac`. No new deps.

## Global Constraints

- **Login disabled by default:** `auth.LOGIN_ENABLED = bool(config.HF_CLIENT_ID and config.HF_CLIENT_SECRET)`. When false, `/auth/*` routes redirect home and no login UI renders. Anonymous voting must work unchanged with login disabled.
- **No network in tests:** all OAuth HTTP goes through injectable `_post`/`_get` params (pattern: `app/integrity.py:_post_form`). Tests never hit huggingface.co.
- **Verified vote definition:** a vote is verified iff its `VoterSession.user_id IS NOT NULL`. The existing trust-gated leaderboard (`VoterSession.trust >= TRUST_THRESHOLD`) is UNCHANGED; verified is an ADDITIONAL scope.
- **CSRF:** the OAuth `state` is generated with `secrets.token_urlsafe`, stored in a short-lived cookie, and must match on callback; a mismatch persists nothing and redirects `/?login=error`.
- **HF endpoints (verified):** authorize `https://huggingface.co/oauth/authorize`; token `https://huggingface.co/oauth/token`; userinfo `https://huggingface.co/oauth/userinfo`; scope `openid profile`; userinfo returns `sub` (id) + `preferred_username`.
- **Never run pytest against a real DB** (temp only; conftest isolates). Full-suite pollution caveat from SP1/dataset applies — prefer get-or-create + `>=`/membership assertions over exact global counts; run the FULL suite before committing.
- Stacks on the SP1 branch (`worktree-scoping-go-public`), extends PR #1.

---

### Task 1: `User` model + `VoterSession.user_id` + auth config

**Files:**

- Modify: `app/models.py` (add `User`; add `VoterSession.user_id`)
- Modify: `app/config.py` (HF OAuth config)
- Test: `tests/test_auth_model.py`

**Interfaces:**

- Produces: `User(id, hf_id, username, created)`; `VoterSession.user_id: int | None`; `config.HF_CLIENT_ID`, `config.HF_CLIENT_SECRET`, `config.SESSION_SECRET`, `config.PUBLIC_BASE_URL`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_model.py
from app.database import SessionLocal, init_db
from app.models import User, VoterSession


def test_user_and_session_link(db_session):
    u = User(hf_id="hf-123", username="alice")
    db_session.add(u); db_session.flush()
    vs = VoterSession(session_id="sess-1", user_id=u.id)
    db_session.add(vs); db_session.flush()
    got = db_session.get(VoterSession, "sess-1")
    assert got.user_id == u.id
    anon = VoterSession(session_id="sess-2")  # user_id defaults NULL = anonymous
    db_session.add(anon); db_session.flush()
    assert db_session.get(VoterSession, "sess-2").user_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_auth_model.py -v`
Expected: FAIL — `AttributeError: type object 'User' has no attribute` / `TypeError: 'user_id' is an invalid keyword argument for VoterSession`.

- [ ] **Step 3: Add the model + config**

In `app/models.py`, add a new model (place near `VoterSession`):

```python
class User(Base):
    """A verified voter, identified by their Hugging Face account (OAuth)."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    hf_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

In `app/models.py`, add a column to `VoterSession` (after its existing columns):

```python
    # Verified-login link: NULL = anonymous, set = signed in with Hugging Face (SP2).
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
```

In `app/config.py`, after the captcha config:

```python
# --- Verified login (Hugging Face OAuth). Off unless client id+secret are set. ---
HF_CLIENT_ID = os.environ.get("BIO3D_HF_CLIENT_ID", "")
HF_CLIENT_SECRET = os.environ.get("BIO3D_HF_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("BIO3D_SESSION_SECRET", "dev-session-secret-change-me")
PUBLIC_BASE_URL = os.environ.get("BIO3D_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth_model.py -v` → PASS
Run: `.venv/bin/pytest -q` → no regressions (new nullable column + table; `init_db` create_all picks it up).

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/config.py tests/test_auth_model.py
git commit -m "feat(auth): User model + VoterSession.user_id link + HF OAuth config"
```

---

### Task 2: `app/auth.py` — OAuth helpers (injectable, no network in tests)

**Files:**

- Create: `app/auth.py`
- Test: `tests/test_auth_helpers.py`

**Interfaces:**

- Produces: `LOGIN_ENABLED: bool`; `AuthError(RuntimeError)`; `new_state() -> str`; `authorize_url(state, redirect_uri) -> str`; `exchange_code(code, redirect_uri, *, _post=_post_form) -> str`; `fetch_userinfo(access_token, *, _get=_get_json) -> dict` (returns `{"hf_id","username"}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_helpers.py
import pytest
from app import auth, config


def test_authorize_url_has_params():
    url = auth.authorize_url("st8", "https://x/auth/callback")
    assert url.startswith("https://huggingface.co/oauth/authorize?")
    assert "state=st8" in url and "scope=openid+profile" in url and "response_type=code" in url


def test_exchange_code_returns_token(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    called = {}
    def fake_post(url, data):
        called["url"] = url; called["data"] = data
        return {"access_token": "tok-abc"}
    assert auth.exchange_code("code123", "https://x/cb", _post=fake_post) == "tok-abc"
    assert called["url"] == "https://huggingface.co/oauth/token"
    assert called["data"]["code"] == "code123"


def test_exchange_code_raises_on_bad_response(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid")
    monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    with pytest.raises(auth.AuthError):
        auth.exchange_code("c", "https://x/cb", _post=lambda u, d: {"error": "bad"})


def test_fetch_userinfo_maps_fields():
    info = auth.fetch_userinfo("tok", _get=lambda url, tok: {"sub": "hf-9", "preferred_username": "bob"})
    assert info == {"hf_id": "hf-9", "username": "bob"}


def test_fetch_userinfo_requires_sub():
    with pytest.raises(auth.AuthError):
        auth.fetch_userinfo("tok", _get=lambda url, tok: {"preferred_username": "bob"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_auth_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 3: Implement `app/auth.py`**

```python
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
    return {"hf_id": str(res["sub"]), "username": res.get("preferred_username") or res.get("name") or ""}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth_helpers.py -v` → PASS (5 passed)
(Note: `scope=openid profile` urlencodes to `scope=openid+profile` — the test asserts the encoded form.)

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth_helpers.py
git commit -m "feat(auth): HF OAuth helpers (authorize/exchange/userinfo, injectable HTTP)"
```

---

### Task 3: `/auth/*` routes + per-request user resolution

**Files:**

- Modify: `app/main.py` (add `/auth/login`, `/auth/callback`, `POST /auth/logout`; resolve `request.state.user` in the session middleware)
- Test: `tests/test_auth_routes.py`

**Interfaces:**

- Consumes: `app.auth` (`authorize_url`, `exchange_code`, `fetch_userinfo`, `new_state`, `AuthError`, `_login_enabled`); `integrity.get_or_create_session`; `app.models.User`.
- Produces: routes above; `request.state.user` = the `User` row (or `None`) for the current session, readable by templates.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_routes.py
from fastapi.testclient import TestClient
from app import auth, config, main
from app.database import SessionLocal
from app.models import User, VoterSession
from sqlalchemy import select


def _client():
    return TestClient(main.app)


def test_login_disabled_redirects_home(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", ""); monkeypatch.setattr(config, "HF_CLIENT_SECRET", "")
    r = _client().get("/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/"


def test_login_enabled_redirects_to_hf(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid"); monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    r = _client().get("/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].startswith("https://huggingface.co/oauth/authorize?")
    assert "bio3d_oauth_state" in r.headers.get("set-cookie", "")


def test_callback_links_user(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid"); monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    monkeypatch.setattr(auth, "exchange_code", lambda code, ru, **k: "tok")
    monkeypatch.setattr(auth, "fetch_userinfo", lambda tok, **k: {"hf_id": "hf-77", "username": "carol"})
    c = _client()
    c.get("/")  # establishes the bio3d_session cookie
    c.get("/auth/login", follow_redirects=False)  # sets bio3d_oauth_state cookie
    state = c.cookies.get("bio3d_oauth_state")
    r = c.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].startswith("/")
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.hf_id == "hf-77")).scalars().first()
        assert u is not None and u.username == "carol"
        sid = c.cookies.get("bio3d_session")
        vs = db.get(VoterSession, sid)
        assert vs is not None and vs.user_id == u.id


def test_callback_state_mismatch_persists_nothing(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid"); monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    c = _client(); c.get("/"); c.get("/auth/login", follow_redirects=False)
    r = c.get("/auth/callback?code=abc&state=WRONG", follow_redirects=False)
    assert r.status_code in (302, 307) and "login=error" in r.headers["location"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_auth_routes.py -v`
Expected: FAIL — 404 for `/auth/login`.

- [ ] **Step 3: Implement routes + middleware user resolution**

In `app/main.py`, extend the `ensure_session` middleware to resolve the current user (add before `return response`):

```python
    # Resolve the verified user (if any) for templates — one light lookup per request.
    request.state.user = None
    try:
        from .database import SessionLocal
        from .models import User, VoterSession
        with SessionLocal() as _db:
            _vs = _db.get(VoterSession, sid)
            if _vs is not None and _vs.user_id is not None:
                request.state.user = _db.get(User, _vs.user_id)
    except Exception:  # noqa: BLE001 — never let user-resolution break a page
        request.state.user = None
```

Add the routes (near the other routes; imports `RedirectResponse` from `fastapi.responses` — add to the existing import if absent):

```python
from fastapi.responses import RedirectResponse  # (add if not already imported)

OAUTH_STATE_COOKIE = "bio3d_oauth_state"


@app.get("/auth/login")
def auth_login(request: Request):
    from . import auth
    if not auth._login_enabled():
        return RedirectResponse("/", status_code=302)
    state = auth.new_state()
    redirect_uri = f"{config.PUBLIC_BASE_URL}/auth/callback"
    resp = RedirectResponse(auth.authorize_url(state, redirect_uri), status_code=302)
    resp.set_cookie(OAUTH_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax")
    return resp


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "",
                  db: Session = Depends(get_db)):
    from . import auth
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not auth._login_enabled() or not code or not state or state != cookie_state:
        resp = RedirectResponse("/?login=error", status_code=302)
        resp.delete_cookie(OAUTH_STATE_COOKIE)
        return resp
    try:
        redirect_uri = f"{config.PUBLIC_BASE_URL}/auth/callback"
        token = auth.exchange_code(code, redirect_uri)
        info = auth.fetch_userinfo(token)
    except auth.AuthError:
        resp = RedirectResponse("/?login=error", status_code=302)
        resp.delete_cookie(OAUTH_STATE_COOKIE)
        return resp
    user = db.execute(select(User).where(User.hf_id == info["hf_id"])).scalars().first()
    if user is None:
        user = User(hf_id=info["hf_id"], username=info["username"])
        db.add(user); db.flush()
    else:
        user.username = info["username"]
    vs = integrity.get_or_create_session(db, request.state.session_id)
    vs.user_id = user.id
    db.commit()
    resp = RedirectResponse("/?login=ok", status_code=302)
    resp.delete_cookie(OAUTH_STATE_COOKIE)
    return resp


@app.post("/auth/logout")
def auth_logout(request: Request, db: Session = Depends(get_db)):
    vs = db.get(VoterSession, request.state.session_id)
    if vs is not None:
        vs.user_id = None
        db.commit()
    return RedirectResponse("/", status_code=302)
```

(Confirm `User`, `VoterSession`, `select`, `integrity`, `Session`, `get_db`, `Request`, `config` are already imported in `main.py` — they are, except possibly `RedirectResponse`; add it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth_routes.py -v` → PASS (4 passed)
Run: `.venv/bin/pytest -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_auth_routes.py
git commit -m "feat(auth): /auth/login|callback|logout + per-request user resolution"
```

---

### Task 4: Verified-tier ranking

**Files:**

- Modify: `app/service.py` (`_matches_for_scope` gains `verified_only`; add `finalize_rows`; add `verified_leaderboard_rows`)
- Modify: `app/main.py` (refactor `_leaderboard_rows` to call `service.finalize_rows`; `/api/leaderboard` gains `verified: bool`)
- Test: `tests/test_verified_ranking.py`

**Interfaces:**

- Consumes: `_matches_for_scope`, `_players_for_scope`, `generator_display_names`, `ranking.bradley_terry`, `ranking.rank_by_ci`.
- Produces: `service.finalize_rows(rows) -> list[dict]` (shared rank+CI geometry); `service.verified_leaderboard_rows(db, criterion_slug="overall", category="all") -> list[dict]` (same row shape as the existing `_leaderboard_rows`); `/api/leaderboard?verified=true` returns it. Existing `_leaderboard_rows` output shape is UNCHANGED (its tests must stay green).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_verified_ranking.py
from app import service
from app.models import (Category, Criterion, Generator, Task, ModelOutput, Comparison,
                        Vote, VoterSession, User)


def _seed_two_generators_one_vote(db, *, verified):
    cat = Category(slug="plant", name="Plant"); crit = Criterion(slug="overall", name="Overall")
    g1 = Generator(slug="g1", name="G1", kind="model"); g2 = Generator(slug="g2", name="G2", kind="model")
    db.add_all([cat, crit, g1, g2]); db.flush()
    t = Task(category_id=cat.id, title="tk", prompt="p", active=True); db.add(t); db.flush()
    o1 = ModelOutput(task_id=t.id, generator_id=g1.id, asset_path="1.glb", source="bio3d-arena")
    o2 = ModelOutput(task_id=t.id, generator_id=g2.id, asset_path="2.glb", source="bio3d-arena")
    db.add_all([o1, o2]); db.flush()
    comp = Comparison(task_id=t.id, output_a_id=o1.id, output_b_id=o2.id, criterion_id=crit.id,
                      session_id="sv")
    db.add(comp); db.flush()
    if verified:
        u = User(hf_id="hf-1", username="v"); db.add(u); db.flush()
        db.add(VoterSession(session_id="sv", user_id=u.id))
    else:
        db.add(VoterSession(session_id="sv"))
    db.add(Vote(comparison_id=comp.id, winner="a", session_id="sv")); db.flush()
    return g1, g2


def test_verified_scope_ranks_verified_votes(db_session):
    _seed_two_generators_one_vote(db_session, verified=True)
    rows = service.verified_leaderboard_rows(db_session, "overall", "all")
    # verified vote produced a decisive game → both generators ranked, each with n_games>0
    assert rows and all(r["n_games"] > 0 for r in rows)
    assert all({"generator", "bt_score", "bt_lower", "bt_upper", "n_games", "rank"} <= set(r) for r in rows)


def test_anonymous_vote_absent_from_verified_scope(db_session):
    _seed_two_generators_one_vote(db_session, verified=False)
    rows = service.verified_leaderboard_rows(db_session, "overall", "all")
    assert rows == []  # no verified votes → no verified games → empty verified board
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_verified_ranking.py -v`
Expected: FAIL — `AttributeError: module 'app.service' has no attribute 'verified_leaderboard_rows'`.

- [ ] **Step 3: Implement**

In `app/service.py`, add a `verified_only` param to `_matches_for_scope` (add the parameter to its signature with default `False`, and add this clause inside the `.where(...)` — after the trust clause):

```python
def _matches_for_scope(
    db: Session, criterion_id: int, category_id: int | None,
    include_ties: bool = False, verified_only: bool = False,
) -> list[tuple[int, int]]:
    ...
    stmt = (
        select(Vote, Comparison)
        .join(Comparison, Vote.comparison_id == Comparison.id)
        .outerjoin(VoterSession, VoterSession.session_id == Vote.session_id)
        .where(
            Comparison.criterion_id == criterion_id,
            Comparison.is_gold.is_(False),
            (VoterSession.trust.is_(None)) | (VoterSession.trust >= config.TRUST_THRESHOLD),
        )
    )
    if verified_only:
        stmt = stmt.where(VoterSession.user_id.is_not(None))
    ...
```

**Extract the shared finalize step (DRY).** The rank + CI-whisker geometry currently lives inline in `_leaderboard_rows` (`app/main.py:382-396`). Move it into a shared helper in `app/service.py` so the verified board renders identically. Add to `app/service.py`:

```python
def finalize_rows(rows: list[dict]) -> list[dict]:
    """Add CI-grouped rank + whisker-bar geometry to leaderboard rows (shared by the
    trusted and verified boards). Rows must have numeric bt_score/bt_lower/bt_upper."""
    rows.sort(key=lambda x: x["bt_score"], reverse=True)
    ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    for row, rank in zip(rows, ranks):
        row["rank"] = rank
    if rows:
        lo = min(r["bt_lower"] for r in rows)
        hi = max(r["bt_upper"] for r in rows)
        span = (hi - lo) or 1.0
        for r in rows:
            r["ci_left"] = round(100.0 * (r["bt_lower"] - lo) / span, 1)
            r["ci_width"] = round(100.0 * (r["bt_upper"] - r["bt_lower"]) / span, 1)
            r["ci_point"] = round(100.0 * (r["bt_score"] - lo) / span, 1)
    return rows
```

Then in `app/main.py:_leaderboard_rows`, REPLACE the inline block (the `rows.sort(...)` through the `ci_point` loop, lines ~381-396) with `return service.finalize_rows(rows)` — behavior-identical; the existing leaderboard tests must stay green.

Now add the verified board to `app/service.py`. **Verified `BTResult` fields are `scores` / `lower` / `upper` / `n_games`** (confirmed in `app/ranking.py:BTResult` — NOT `lowers`/`uppers`). Row shape matches `_leaderboard_rows` (`generator` = display NAME, `kind`, `bt_score`, `bt_lower`, `bt_upper`, `n_games`, + rank/ci from `finalize_rows`). Only include generators that actually have a verified game (else the board is empty and CI geometry has nothing to scale):

```python
def verified_leaderboard_rows(db: Session, criterion_slug: str = "overall",
                              category: str = "all") -> list[dict]:
    """On-demand Bradley-Terry over VERIFIED votes only (session.user_id set). Not cached."""
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    category_id = None
    if category != "all":
        cat = db.execute(select(Category).where(Category.slug == category)).scalars().first()
        category_id = cat.id if cat else None
    players = _players_for_scope(db, category_id)
    matches = _matches_for_scope(db, crit.id, category_id, verified_only=True)
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP)
    names = generator_display_names(db)
    rows = []
    for gid in players:
        if result.n_games.get(gid, 0) <= 0:
            continue  # only generators with an actual verified game appear on the verified board
        gen = db.get(Generator, gid)
        rows.append(
            {
                "generator": names.get(gid, gen.name if gen else str(gid)),
                "kind": gen.kind if gen else "model",
                "bt_score": round(result.scores.get(gid, 0.0), 1),
                "bt_lower": round(result.lower.get(gid, 0.0), 1),
                "bt_upper": round(result.upper.get(gid, 0.0), 1),
                "n_games": result.n_games.get(gid, 0),
            }
        )
    if not rows:
        return []
    return finalize_rows(rows)
```

In `app/main.py`, extend the route:

```python
@app.get("/api/leaderboard")
def api_leaderboard(db: Session = Depends(get_db), criterion: str = "overall",
                    category: str = "all", verified: bool = False):
    if verified:
        rows = service.verified_leaderboard_rows(db, criterion, category)
    else:
        rows = _leaderboard_rows(db, criterion, category)
    return {"criterion": criterion, "category": category, "verified": verified, "rows": rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_verified_ranking.py -v` → PASS
Run: `.venv/bin/pytest -q` → no regressions (existing `/api/leaderboard` callers unaffected — `verified` defaults false).

- [ ] **Step 5: Commit**

```bash
git add app/service.py app/main.py tests/test_verified_ranking.py
git commit -m "feat(auth): verified-only leaderboard scope (BT over verified votes)"
```

---

### Task 5: Nav UI — login / status / verified badge

**Files:**

- Modify: `app/templates/base.html` (nav: login link / signed-in status)
- Test: `tests/test_auth_ui.py`

**Interfaces:**

- Consumes: `request.state.user` (set by the Task 3 middleware); `auth._login_enabled()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_ui.py
from fastapi.testclient import TestClient
from app import auth, config, main


def test_login_link_shown_when_enabled_and_logged_out(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", "cid"); monkeypatch.setattr(config, "HF_CLIENT_SECRET", "sec")
    r = TestClient(main.app).get("/")
    assert r.status_code == 200 and "/auth/login" in r.text


def test_no_login_ui_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "HF_CLIENT_ID", ""); monkeypatch.setattr(config, "HF_CLIENT_SECRET", "")
    r = TestClient(main.app).get("/")
    assert r.status_code == 200 and "/auth/login" not in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_auth_ui.py -v`
Expected: FAIL — `/auth/login` not present (nav has no login UI yet).

- [ ] **Step 3: Add the nav UI**

The nav needs to know if login is enabled. In `app/main.py`'s `ensure_session` middleware (Task 3), also set `request.state.login_enabled = auth._login_enabled()` (import `auth` locally). Then in `app/templates/base.html`, inside the `<nav>` block, add:

```html
{% if request.state.user %}
<span class="verified">✓ {{ request.state.user.username }}</span>
<form method="post" action="/auth/logout" style="display:inline">
  <button type="submit">Sign out</button>
</form>
{% elif request.state.login_enabled %}
<a href="/auth/login">Sign in with Hugging Face</a>
{% endif %}
```

(`request.state.user`/`login_enabled` are safe to reference — the middleware sets both on every request. If a template renders outside the middleware in a test, guard with `request.state.get('user', None)` style is not available on Starlette `State`; the middleware always runs for `TestClient`, so plain access is fine.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth_ui.py -v` → PASS
Run: `.venv/bin/pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/templates/base.html tests/test_auth_ui.py
git commit -m "feat(auth): nav login link / signed-in status + verified badge"
```

---

## Self-Review

**Spec coverage:**

- `app/auth.py` OAuth helpers (injectable) → Task 2. ✓
- `User` + `VoterSession.user_id` + config → Task 1. ✓
- `/auth/login|callback|logout` + CSRF state + user resolution → Task 3. ✓
- Verified-tier ranking (verified scope + reuse trust join) → Task 4. ✓
- Nav UI + login-disabled-hides-UI → Task 5. ✓
- Login disabled by default; anonymous unaffected; no network in tests → Global Constraints + Tasks 2/3/5 tests. ✓
- Error handling (state mismatch → `?login=error`, no 500) → Task 3 test. ✓

**Placeholder scan:** no TBD/TODO; complete code in every step. `BTResult` fields (`scores`/`lower`/`upper`/`n_games`) and the `_leaderboard_rows` row shape (`generator`=display name, `kind`, `bt_score`/`bt_lower`/`bt_upper`, `n_games`, + `rank`/`ci_*` from `finalize_rows`) are CONFIRMED against live source (`app/ranking.py`, `app/main.py:348-397`). Remaining adjust-on-contact: Task 3 — confirm `RedirectResponse` is imported in `main.py` (add if absent).

**Type consistency:** `verified_leaderboard_rows(db, criterion_slug, category)` signature matches its call in `/api/leaderboard` and the tests; `exchange_code`/`fetch_userinfo`/`authorize_url`/`new_state`/`AuthError`/`_login_enabled` names identical across `app/auth.py` (Task 2) and the routes (Task 3); `VoterSession.user_id` + `User.hf_id`/`username` identical across Tasks 1/3/4/5; `OAUTH_STATE_COOKIE`/`bio3d_oauth_state` consistent between Task 3 route and its test.
