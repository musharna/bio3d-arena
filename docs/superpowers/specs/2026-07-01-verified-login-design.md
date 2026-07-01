# SP2 — Hugging Face Verified Login (design)

> 2026-07-01. The one genuinely-new build in the go-public roadmap: optional verified login to
> grow vote volume AND strengthen integrity. Stacks on SP1 (extends PR #1). Anonymous voting is
> unchanged; login is optional and disabled by default until HF OAuth secrets are configured.

## Goal

Let voters optionally sign in with Hugging Face. Verified votes form a higher-integrity pool
(a "verified" leaderboard scope), which both scales participation (low-friction — anonymous
still works) and neutralizes part of the "Leaderboard Illusion" critique. No gating of anonymous
voting.

## Decisions (locked)

- **Provider: Hugging Face OAuth2/OIDC** (fits the ML/gen-3D community; what 3D Arena uses).
- **Policy: optional verified tier** — anonymous voting stays; logging in marks a voter's session
  as verified; verified votes get their own leaderboard scope + counts. No required login.

## HF OAuth (verified endpoints)

- Authorize: `https://huggingface.co/oauth/authorize` — params `client_id`, `redirect_uri`,
  `scope=openid profile`, `response_type=code`, `state`.
- Token: `POST https://huggingface.co/oauth/token` — exchange `code` → access token.
- Userinfo: `GET https://huggingface.co/oauth/userinfo` — returns `sub` (stable user id) +
  `preferred_username` (HF username). (Refs: huggingface.co/docs/hub/oauth.)

## Components

### 1. `app/auth.py` — OAuth helpers (pure/injectable, testable)

- `LOGIN_ENABLED` = `bool(config.HF_CLIENT_ID and config.HF_CLIENT_SECRET)`.
- `authorize_url(state: str, redirect_uri: str) -> str` — builds the HF authorize URL.
- `exchange_code(code, redirect_uri, *, _post=…) -> str` — POSTs to the token endpoint, returns
  the access token. `_post` injectable (stdlib urllib, like `integrity._post_form`); network/parse
  failure raises `AuthError`.
- `fetch_userinfo(access_token, *, _get=…) -> dict` — GETs userinfo, returns
  `{"hf_id": sub, "username": preferred_username}`. `_get` injectable.
- `new_state() -> str` — CSRF state (from `secrets.token_urlsafe`; NOTE scripts can't call random,
  but this runs in the live server, not a workflow script — the server may use `secrets`).

### 2. Data model (`app/models.py`)

- New `User`: `id` (pk), `hf_id` (String, unique, index), `username` (String), `created` (DateTime).
- `VoterSession.user_id`: nullable FK → `user.id`, index. `NULL` = anonymous; set = verified.
  (create_all-only schema; adding a nullable column is safe, consistent with the codebase.)

### 3. Routes (`app/main.py`)

- `GET /auth/login` — if `LOGIN_ENABLED`: mint `state`, set it in a short-lived signed cookie,
  redirect to `authorize_url(state, redirect_uri)`. If disabled: redirect home.
- `GET /auth/callback` — verify `state` matches the cookie (CSRF); `exchange_code`; `fetch_userinfo`;
  upsert `User` by `hf_id`; `integrity.get_or_create_session(db, request.state.session_id)` and set
  its `user_id`; redirect home. Any error (state mismatch, HF failure) → redirect home with a
  `?login=error` flag, never a 500.
- `POST /auth/logout` — clear the current session's `VoterSession.user_id` (or rotate to a fresh
  anonymous session cookie); redirect home.
- `redirect_uri` = `config.PUBLIC_BASE_URL + "/auth/callback"` (new config; defaults to local).

### 4. Verified-tier ranking (`app/service.py`)

A vote is **verified** iff its session's `VoterSession.user_id IS NOT NULL`. Reuse the existing
`Vote`↔`VoterSession` outerjoin (service.py:151). Add a **`verified`-scoped** Bradley-Terry
leaderboard: the same computation filtered to verified votes, exposed alongside the existing
trusted leaderboard, plus a per-generator verified-vote count. The existing trust-gated leaderboard
is unchanged.

### 5. UI (`app/templates/base.html` nav)

- Logged out + `LOGIN_ENABLED`: a "Sign in with Hugging Face" link (`/auth/login`).
- Logged in: "signed in as {username} ✓" + a logout control. A verified badge.
- `LOGIN_ENABLED` false: no login UI (parallel to captcha-off).
- The current user is resolved per-request from `VoterSession.user_id` → `User` and passed to
  templates (a small `request.state.user` set in middleware or a helper).

## Data flow

```
click "Sign in" → GET /auth/login (state cookie) → HF authorize → HF redirects to
GET /auth/callback?code&state → verify state → exchange_code → fetch_userinfo →
upsert User(hf_id, username) → VoterSession(session_id).user_id = user.id → home.
Subsequent votes from that cookie session are VERIFIED (user_id set).
POST /auth/logout → user_id = NULL → home.
```

## Error handling

- **Login disabled** (no secrets): `/auth/login` + `/auth/callback` redirect home; no UI shown.
- **State/CSRF mismatch or HF error**: redirect home `?login=error`; no 500; nothing persisted.
- **Userinfo missing `sub`**: treat as auth failure (don't create a null-id user).

## Testing

- **Unit** (`app/auth.py`): `authorize_url` contains client_id/redirect/scope/state;
  `exchange_code` with injected `_post` returns the token, and raises `AuthError` on failure;
  `fetch_userinfo` with injected `_get` maps `sub`/`preferred_username` → `hf_id`/`username`;
  `LOGIN_ENABLED` reflects config.
- **Callback flow**: with `auth.exchange_code`/`fetch_userinfo` monkeypatched, a state-valid
  callback upserts a `User`, links the session's `VoterSession.user_id`, and redirects home; a
  state-mismatch callback persists nothing and redirects `?login=error`.
- **Verified ranking**: a verified vote (session with `user_id`) appears in the `verified` scope;
  an anonymous vote does not; the trusted leaderboard is unchanged.
- **Anonymous unaffected / login-disabled**: with no secrets, voting works and no login UI renders.
- All OAuth network calls are injected — tests never hit huggingface.co.

## Open decisions (defaults chosen)

1. **Logout** = clear `user_id` on the current session (simplest; keeps the cookie). Default.
2. **State cookie** signed with `BIO3D_ADMIN_TOKEN`-derived key or a dedicated
   `BIO3D_SESSION_SECRET`; default = a dedicated secret, required only when `LOGIN_ENABLED`.
3. **Verified leaderboard exposure** = a scope toggle on the existing leaderboard page + a count;
   a fully separate `/verified` page is deferred (YAGNI).

## Out of scope / deferred

Multiple OAuth providers, per-user profile pages, per-user vote history UI, org-scoped access,
avatar display. HF OAuth **app registration + secrets** is a manual operator step (like the
captcha keys) — code ships the seam, disabled by default.

## Manual operator step (documented, not built)

Register an OAuth app at huggingface.co (Settings → Connected Apps / OAuth), set the callback to
`<public-base>/auth/callback`, and set `BIO3D_HF_CLIENT_ID` / `BIO3D_HF_CLIENT_SECRET` /
`BIO3D_SESSION_SECRET` + `BIO3D_PUBLIC_BASE_URL` in the deploy env.
