# Bad-output handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep clearly-failed / not-a-plant outputs out of the vote pool via a completeness-category auto-gate, and give voters a per-model flag button that auto-hides an output after K distinct-session flags (admin-reviewable).

**Architecture:** Two layers sharing the existing `exclude_fn` pool-gate seam. (1) Auto-gate: fold a `{isolated-organ, fragment}` completeness-category set + a new `ModelOutput.hidden_at` column into `_build_comparison._vote_excluded`, preserving pick_task/pick_pair parity. (2) Human flag: a new `OutputFlag` table + `POST /api/flag` (rate-limited, per-session dedup, auto-hide at K), a ⚑ button in the viewer control bar wired from arena.js, `output_id` added to the `/api/next` payload, and admin Restore/Confirm-hide in `/admin/moderation`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Pydantic v2, Jinja2, vanilla JS (model-viewer). Tests: `.venv/bin/pytest` via FastAPI `TestClient`.

## Global Constraints

- Test runner: `.venv/bin/pytest` (run with `PYTHONPATH="$(pwd)"`, `BIO3D_DATABASE_URL` UNSET). Baseline: **642 passed, 8 skipped**.
- **NEVER** set `BIO3D_DATABASE_URL=study` — the suite drops tables (incident 2026-06-28).
- Auto-gate excluded categories: **`{"isolated-organ", "fragment"}`** (keep `complete`, `partial-organism`, and unscored outputs).
- Flag auto-hide threshold **K = `config.FLAG_HIDE_THRESHOLD`** (env `BIO3D_FLAG_HIDE_THRESHOLD`, default **3**), counted over **distinct sessions**.
- Flag reasons allowed set: **`not_a_plant | failed | other`**.
- Reuse verbatim (do not reimplement): `integrity.check_rate_limit(session_id) -> bool`, `integrity.reset_rate_limits()`, `_require_admin(token)`, `require_admin_query`, the `exclude_fn` seam + pick_task/pick_pair parity, `_utcnow()` from `app/models.py` (tz-aware UTC), the arena.js non-silent-failure vote pattern.
- **Read `Completeness.category` only** — do NOT edit the D-Complete scorer (another agent's file).
- Flag button is **decoupled** from voting (does not cast a vote or advance). No public flag-count display (anti-brigading).
- New schema (`ModelOutput.hidden_at`, `OutputFlag`) is created by `create_all` on a fresh DB; existing DBs get it via deploy re-import (same schema-drift story as `voter_session.user_id`).

## File Structure

- `app/config.py` — MODIFY: add `POOL_EXCLUDED_COMPLETENESS_CATEGORIES`, `FLAG_HIDE_THRESHOLD`.
- `app/models.py` — MODIFY: add `ModelOutput.hidden_at`; add `OutputFlag` model.
- `app/flags.py` — CREATE: pure-ish DB helpers (`excluded_output_ids_by_completeness`, `record_flag`, `distinct_flag_count`).
- `app/schemas.py` — MODIFY: add `FlagIn`.
- `app/main.py` — MODIFY: extend `_build_comparison._vote_excluded`; add `output_id` to `_serialize`; add `POST /api/flag`; extend `moderation_page` + add Restore/Confirm-hide routes.
- `app/static/viewer.js` — MODIFY: add ⚑ control in `addControls` (via an `onFlag` hook).
- `app/static/arena.js` — MODIFY: stash slot `output_id`s; wire the ⚑ button to `POST /api/flag`.
- `app/templates/moderation.html` — MODIFY: add a flagged/hidden-outputs section with Restore/Confirm-hide forms.
- Tests: `tests/test_flags_service.py`, `tests/test_pool_autogate.py`, `tests/test_flag_api.py`, `tests/test_flag_admin.py`, and additions to `tests/test_synced_rotation.py`-style client-JS assertions in a new `tests/test_flag_client.py`.

---

### Task 1: Schema + config (hidden_at, OutputFlag, tunables)

**Files:**

- Modify: `app/config.py`
- Modify: `app/models.py:95-121` (ModelOutput) and end of file (new model)
- Test: `tests/test_flags_schema.py`

**Interfaces:**

- Produces: `ModelOutput.hidden_at: Mapped[dt.datetime | None]`; `OutputFlag(id, output_id, session_id, reason, created)`; `config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES: set[str]`; `config.FLAG_HIDE_THRESHOLD: int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flags_schema.py
from __future__ import annotations

from app import config
from app.database import SessionLocal, init_db
from app.models import ModelOutput, OutputFlag


def setup_module(_m):
    init_db()


def test_config_defaults():
    assert config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES == {"isolated-organ", "fragment"}
    assert config.FLAG_HIDE_THRESHOLD == 3


def test_hidden_at_and_outputflag_exist():
    with SessionLocal() as db:
        cols = {c.name for c in ModelOutput.__table__.columns}
        assert "hidden_at" in cols
        fcols = {c.name for c in OutputFlag.__table__.columns}
        assert fcols == {"id", "output_id", "session_id", "reason", "created"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flags_schema.py -v`
Expected: FAIL (`AttributeError`/`ImportError` — `OutputFlag` and config names not defined).

- [ ] **Step 3: Add config constants**

In `app/config.py`, after the `TRUST_THRESHOLD` block (around line 32), add:

```python
# --- Bad-output handling ---
# Vote pool drops outputs D-Complete classified into these completeness categories
# (clearly not a whole plant). Empty set disables the completeness auto-gate.
POOL_EXCLUDED_COMPLETENESS_CATEGORIES = {
    c.strip()
    for c in os.environ.get(
        "BIO3D_POOL_EXCLUDED_COMPLETENESS_CATEGORIES", "isolated-organ,fragment"
    ).split(",")
    if c.strip()
}
# Distinct-session flags on an output before it auto-hides (pending admin review).
FLAG_HIDE_THRESHOLD = int(os.environ.get("BIO3D_FLAG_HIDE_THRESHOLD", "3"))
```

- [ ] **Step 4: Add hidden_at column + OutputFlag model**

In `app/models.py`, inside `class ModelOutput` after the `external_url` line (118), add:

```python
    # Non-null ⇒ pulled from the vote pool (auto-hidden at K flags, or by admin). Nullable so
    # every existing/new output defaults to visible.
    hidden_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
```

At the end of `app/models.py`, add the new model:

```python
class OutputFlag(Base):
    """A human report that an output is not a plant / failed. Distinct-session count of these
    on one output drives auto-hide; the rows are also a human-labeled failure dataset."""

    __tablename__ = "output_flag"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(String(32), default="not_a_plant")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

(`ForeignKey`, `String`, `DateTime`, `Mapped`, `mapped_column`, `_utcnow`, `dt` are already imported in models.py.)

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flags_schema.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/models.py tests/test_flags_schema.py
git commit -m "feat(bad-output): hidden_at column + OutputFlag model + auto-gate/flag config"
```

---

### Task 2: flags service helpers

**Files:**

- Create: `app/flags.py`
- Test: `tests/test_flags_service.py`

**Interfaces:**

- Consumes: `OutputFlag`, `ModelOutput`, `Completeness` (Task 1 + existing); `_utcnow`.
- Produces:
  - `excluded_output_ids_by_completeness(db, categories: set[str]) -> set[int]`
  - `distinct_flag_count(db, output_id: int) -> int`
  - `record_flag(db, output_id: int, session_id: str, reason: str, threshold: int) -> tuple[bool, int]` (returns `(hidden, distinct_count)`; commits nothing — caller commits)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flags_service.py
from __future__ import annotations

import uuid

from app import flags
from app.database import SessionLocal, init_db
from app.models import Completeness, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _output(db, category=None):
    g = Generator(slug=f"fg-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"ft-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
    db.add(o)
    db.flush()
    if category is not None:
        db.add(Completeness(output_id=o.id, category=category, score=0.0, scorer_version="v1"))
        db.flush()
    return o


def test_excluded_by_completeness():
    with SessionLocal() as db:
        frag = _output(db, "fragment")
        iso = _output(db, "isolated-organ")
        good = _output(db, "complete")
        partial = _output(db, "partial-organism")
        unscored = _output(db, None)
        db.commit()
        ex = flags.excluded_output_ids_by_completeness(db, {"isolated-organ", "fragment"})
        assert frag.id in ex and iso.id in ex
        assert good.id not in ex and partial.id not in ex and unscored.id not in ex


def test_excluded_uses_latest_completeness():
    with SessionLocal() as db:
        o = _output(db, "fragment")  # older
        db.add(Completeness(output_id=o.id, category="complete", score=1.0, scorer_version="v2"))
        db.commit()
        ex = flags.excluded_output_ids_by_completeness(db, {"isolated-organ", "fragment"})
        assert o.id not in ex  # latest says complete


def test_record_flag_dedup_and_autohide():
    with SessionLocal() as db:
        o = _output(db)
        db.commit()
        hidden, n = flags.record_flag(db, o.id, "s1", "not_a_plant", threshold=3)
        assert (hidden, n) == (False, 1)
        # same session again → no double count
        hidden, n = flags.record_flag(db, o.id, "s1", "not_a_plant", threshold=3)
        assert (hidden, n) == (False, 1)
        flags.record_flag(db, o.id, "s2", "failed", threshold=3)
        hidden, n = flags.record_flag(db, o.id, "s3", "not_a_plant", threshold=3)
        db.commit()
        assert (hidden, n) == (True, 3)
        assert db.get(ModelOutput, o.id).hidden_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flags_service.py -v`
Expected: FAIL (`ModuleNotFoundError: app.flags`).

- [ ] **Step 3: Write the implementation**

```python
# app/flags.py
"""Bad-output helpers: completeness-based pool exclusion + human flag recording."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Completeness, ModelOutput, OutputFlag, _utcnow


def excluded_output_ids_by_completeness(db: Session, categories: set[str]) -> set[int]:
    """Output ids whose LATEST completeness row is in `categories`. Empty categories → empty set."""
    if not categories:
        return set()
    latest: dict[int, tuple] = {}
    for oid, cat, computed in db.execute(
        select(Completeness.output_id, Completeness.category, Completeness.computed)
    ).all():
        if oid not in latest or computed > latest[oid][1]:
            latest[oid] = (cat, computed)
    return {oid for oid, (cat, _) in latest.items() if cat in categories}


def distinct_flag_count(db: Session, output_id: int) -> int:
    """Number of DISTINCT sessions that have flagged this output."""
    return int(
        db.execute(
            select(func.count(func.distinct(OutputFlag.session_id))).where(
                OutputFlag.output_id == output_id
            )
        ).scalar_one()
    )


def record_flag(
    db: Session, output_id: int, session_id: str, reason: str, threshold: int
) -> tuple[bool, int]:
    """Record one flag (idempotent per (output, session)); auto-hide at `threshold` distinct
    sessions. Returns (is_hidden, distinct_count). Caller commits."""
    existing = (
        db.execute(
            select(OutputFlag).where(
                OutputFlag.output_id == output_id, OutputFlag.session_id == session_id
            )
        )
        .scalars()
        .first()
    )
    if existing is None:
        db.add(OutputFlag(output_id=output_id, session_id=session_id, reason=reason))
        db.flush()
    count = distinct_flag_count(db, output_id)
    out = db.get(ModelOutput, output_id)
    if out is not None and out.hidden_at is None and count >= threshold:
        out.hidden_at = _utcnow()
    return (out is not None and out.hidden_at is not None), count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flags_service.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/flags.py tests/test_flags_service.py
git commit -m "feat(bad-output): flags service (completeness exclusion + dedup flag recording)"
```

---

### Task 3: auto-gate + hidden wiring into the vote pool

**Files:**

- Modify: `app/main.py:260-285` (`_build_comparison`)
- Test: `tests/test_pool_autogate.py`

**Interfaces:**

- Consumes: `flags.excluded_output_ids_by_completeness`, `config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES`, `ModelOutput.hidden_at`.
- Produces: pool that also excludes `{isolated-organ, fragment}` + `hidden_at IS NOT NULL`, with pick_task/pick_pair parity preserved.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pool_autogate.py
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app import config
from app.database import SessionLocal
from app.main import app, _build_comparison
from app.models import Category, Completeness, Generator, ModelOutput, Task

client = TestClient(app)


def _task_with(db, cats):
    """A task whose only paradigm group is `cats` (one output per completeness category)."""
    cat = Category(slug=f"pg-{uuid.uuid4().hex[:8]}", name="C")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=f"pg-{uuid.uuid4().hex[:8]}", prompt="p")
    db.add(t)
    db.flush()
    outs = []
    for c in cats:
        g = Generator(slug=f"pg-{uuid.uuid4().hex}", name="g", kind="model", paradigm="same")
        db.add(g)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
        db.add(o)
        db.flush()
        if c is not None:
            db.add(Completeness(output_id=o.id, category=c, score=0.0, scorer_version="v1"))
        outs.append(o)
    db.commit()
    return t, cat, outs


def test_pool_excludes_bad_categories_but_keeps_good():
    with SessionLocal() as db:
        t, cat, outs = _task_with(db, ["complete", "complete", "fragment", "isolated-organ"])
        bad = {outs[2].id, outs[3].id}
        for _ in range(30):
            payload = _build_comparison(db, f"s-{uuid.uuid4().hex}", None, cat.slug)
            if payload is None:
                continue
            comp_id = payload["comparison_id"]
            from app.models import Comparison

            c = db.get(Comparison, comp_id)
            assert not ({c.output_a_id, c.output_b_id} & bad)


def test_pool_excludes_hidden():
    with SessionLocal() as db:
        from app.models import Comparison, _utcnow

        t, cat, outs = _task_with(db, ["complete", "complete", "complete"])
        outs[0].hidden_at = _utcnow()
        db.commit()
        for _ in range(30):
            payload = _build_comparison(db, f"s-{uuid.uuid4().hex}", None, cat.slug)
            if payload is None:
                continue
            c = db.get(Comparison, payload["comparison_id"])
            assert outs[0].id not in {c.output_a_id, c.output_b_id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_pool_autogate.py -v`
Expected: FAIL (bad/hidden outputs still served — the gate isn't wired).

- [ ] **Step 3: Wire the gate into `_build_comparison`**

In `app/main.py`, the `_vote_excluded` block currently reads (around 260-277):

```python
    from .sourcing import is_reference_scan, is_untextured_output

    category_id = _resolve_category_id(db, category_slug)

    def _vote_excluded(o):
        return is_reference_scan(o.source) or is_untextured_output(o)
```

Replace with:

```python
    from .sourcing import is_reference_scan, is_untextured_output
    from . import flags

    category_id = _resolve_category_id(db, category_slug)

    # Precompute the completeness-gated output ids ONCE (per-output exclude_fn stays O(1)).
    _gated = flags.excluded_output_ids_by_completeness(
        db, config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES
    )

    def _vote_excluded(o):
        return (
            is_reference_scan(o.source)
            or is_untextured_output(o)
            or o.hidden_at is not None
            or o.id in _gated
        )
```

(`config` is already imported in main.py.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_pool_autogate.py -v`
Expected: PASS (2 tests). Then run the matchmaking parity guard to confirm no 404 regression:
Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_matchmaking_exclude.py tests/test_matchmaking_voted.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_pool_autogate.py
git commit -m "feat(bad-output): auto-gate vote pool on completeness category + hidden_at"
```

---

### Task 4: POST /api/flag endpoint

**Files:**

- Modify: `app/schemas.py` (add `FlagIn`)
- Modify: `app/main.py` (new route near `api_vote`, ~line 438)
- Test: `tests/test_flag_api.py`

**Interfaces:**

- Consumes: `flags.record_flag`, `integrity.check_rate_limit`, `config.FLAG_HIDE_THRESHOLD`, `request.state.session_id`.
- Produces: `POST /api/flag` returning `{"status": "ok", "hidden": bool, "flags": int}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flag_api.py
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app import integrity
from app.database import SessionLocal
from app.main import app
from app.models import Generator, ModelOutput, Task

client = TestClient(app)


def _output():
    with SessionLocal() as db:
        g = Generator(slug=f"fa-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
        db.add(g)
        db.flush()
        t = Task(title=f"fa-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
        db.add(t)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
        db.add(o)
        db.commit()
        return o.id


def test_flag_records_and_dedups():
    integrity.reset_rate_limits()
    oid = _output()
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_a_plant"})
    assert r.status_code == 200 and r.json() == {"status": "ok", "hidden": False, "flags": 1}
    # same session (same TestClient cookie) again → no double count
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_a_plant"})
    assert r.json()["flags"] == 1


def test_flag_unknown_output_404():
    integrity.reset_rate_limits()
    r = client.post("/api/flag", json={"output_id": 999999, "reason": "failed"})
    assert r.status_code == 404


def test_flag_bad_reason_422():
    integrity.reset_rate_limits()
    oid = _output()
    r = client.post("/api/flag", json={"output_id": oid, "reason": "banana"})
    assert r.status_code == 422


def test_flag_autohide_at_threshold():
    integrity.reset_rate_limits()
    oid = _output()
    from app import flags

    with SessionLocal() as db:
        flags.record_flag(db, oid, "other-1", "failed", threshold=99)
        flags.record_flag(db, oid, "other-2", "failed", threshold=99)
        db.commit()
    # third flag from THIS client session crosses the default K=3
    r = client.post("/api/flag", json={"output_id": oid, "reason": "not_a_plant"})
    assert r.json() == {"status": "ok", "hidden": True, "flags": 3}
    with SessionLocal() as db:
        assert db.get(ModelOutput, oid).hidden_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flag_api.py -v`
Expected: FAIL (404 route not found → assertions fail / `FlagIn` missing).

- [ ] **Step 3: Add `FlagIn` schema**

In `app/schemas.py`, after `VoteIn`:

```python
class FlagIn(BaseModel):
    output_id: int
    reason: str = Field(default="not_a_plant", pattern="^(not_a_plant|failed|other)$")
```

- [ ] **Step 4: Add the route**

In `app/main.py`: extend the schema import (line 55) to include `FlagIn`:

```python
from .schemas import CategoryIn, FlagIn, GeneratorIn, TaskIn, VoteIn
```

Add the route immediately after `api_vote` (after its `return {"status": "ok", "next": nxt}`, ~line 438):

```python
@app.post("/api/flag")
def api_flag(flag_in: FlagIn, request: Request, db: Session = Depends(get_db)):
    """Report an output as not-a-plant / failed. Rate-limited; one flag per session per output;
    auto-hides the output at FLAG_HIDE_THRESHOLD distinct sessions. Not a vote — never advances."""
    from . import flags

    sid = request.state.session_id
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if db.get(ModelOutput, flag_in.output_id) is None:
        raise HTTPException(404, "Unknown output")
    hidden, count = flags.record_flag(
        db, flag_in.output_id, sid, flag_in.reason, config.FLAG_HIDE_THRESHOLD
    )
    db.commit()
    return {"status": "ok", "hidden": hidden, "flags": count}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flag_api.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add app/schemas.py app/main.py tests/test_flag_api.py
git commit -m "feat(bad-output): POST /api/flag (rate-limited, per-session dedup, auto-hide at K)"
```

---

### Task 5: /api/next payload output_id + client ⚑ flag button

**Files:**

- Modify: `app/main.py:193-203` (`_serialize`)
- Modify: `app/static/viewer.js:40-62` (`addControls`) and `mount(...)` signature
- Modify: `app/static/arena.js:103-131` (`render`)
- Test: `tests/test_flag_client.py`

**Interfaces:**

- Consumes: `POST /api/flag`.
- Produces: `/api/next` payload `a`/`b` each gain `"output_id": int`; a ⚑ control that POSTs `{output_id, reason:"not_a_plant"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flag_client.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_m):
    seed_all(force=True)


def test_next_payload_has_output_id_and_no_generator_leak():
    data = client.get("/api/next").json()
    assert "output_id" in data["a"] and "output_id" in data["b"]
    assert isinstance(data["a"]["output_id"], int)
    assert "generator" not in str(data).lower()  # still anonymized


def test_viewer_and_arena_wire_the_flag_button():
    vjs = client.get("/static/viewer.js").text
    assert "onFlag" in vjs  # control hook exists
    ajs = client.get("/static/arena.js").text
    assert "/api/flag" in ajs and "output_id" in ajs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flag_client.py -v`
Expected: FAIL (no `output_id` in payload; `onFlag`/`/api/flag` absent).

- [ ] **Step 3: Add output_id to `_serialize`**

In `app/main.py`, `_serialize` return (lines 201-202), change the `a`/`b` dicts to include `output_id`:

```python
        "a": {
            "url": storage.url_for(out_a.asset_path),
            "format": out_a.asset_format,
            "output_id": out_a.id,
        },
        "b": {
            "url": storage.url_for(out_b.asset_path),
            "format": out_b.asset_format,
            "output_id": out_b.id,
        },
```

- [ ] **Step 4: Add the ⚑ control to viewer.js**

In `app/static/viewer.js`, change `addControls(slot)` (line 40) to `addControls(slot, onFlag)` and append a flag button before the closing `slot.appendChild(bar)`:

```javascript
function addControls(slot, onFlag) {
  const bar = document.createElement("div");
  bar.className = "viewer-controls";
  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "viewer-ctl";
  reset.setAttribute("aria-label", "Reset view");
  reset.title = "Reset view";
  reset.textContent = "⟳";
  reset.addEventListener("click", () => {
    if (slot._resetView) slot._resetView();
  });
  const fs = document.createElement("button");
  fs.type = "button";
  fs.className = "viewer-ctl";
  fs.setAttribute("aria-label", "Fullscreen");
  fs.title = "Fullscreen";
  fs.textContent = "⛶";
  fs.addEventListener("click", () => toggleFullscreen(slot));
  bar.appendChild(reset);
  bar.appendChild(fs);
  if (onFlag) {
    const flag = document.createElement("button");
    flag.type = "button";
    flag.className = "viewer-ctl";
    flag.setAttribute("aria-label", "Flag: not a plant / failed");
    flag.title = "Flag: not a plant / failed";
    flag.textContent = "⚑";
    flag.addEventListener("click", () => onFlag(flag));
    bar.appendChild(flag);
  }
  slot.appendChild(bar);
}
```

Thread `onFlag` through `mount` and the two `addControls` call sites. Change `mount(slot, asset)` (line 165) to `mount(slot, asset, onFlag)` and pass `onFlag` into `mountMesh`/`mountMolecular`:

```javascript
function mount(slot, asset, onFlag) {
  slot.innerHTML = "";
  slot._viewerGen = (slot._viewerGen || 0) + 1;
  const fmt = (asset.format || "glb").toLowerCase();
  if (MOL.has(fmt)) mountMolecular(slot, asset, fmt, onFlag);
  else if (MESH.has(fmt)) mountMesh(slot, asset, onFlag);
  else failed(slot, "Unsupported format: " + fmt);
  return fmt;
}
```

Change `mountMesh(slot, asset)` → `mountMesh(slot, asset, onFlag)` and its `addControls(slot);` → `addControls(slot, onFlag);`. Same for `mountMolecular(slot, asset, fmt)` → `mountMolecular(slot, asset, fmt, onFlag)` and its `addControls(slot);` → `addControls(slot, onFlag);`.

- [ ] **Step 5: Wire the button in arena.js**

In `app/static/arena.js`, replace the `render` viewer-mount block (lines 119-128) with one that passes an `onFlag` hook carrying each slot's `output_id`:

```javascript
// Shared viewer registry (viewer.js) picks model-viewer vs 3Dmol by format.
el("fmt-a").textContent = window.Taxon3DViewer.mount(
  el("slot-a"),
  data.a,
  (btn) => flagOutput(data.a.output_id, btn),
).toUpperCase();
el("fmt-b").textContent = window.Taxon3DViewer.mount(
  el("slot-b"),
  data.b,
  (btn) => flagOutput(data.b.output_id, btn),
).toUpperCase();
window.Taxon3DViewer.syncPair(el("slot-a"), el("slot-b"));
```

Add the `flagOutput` function (near `vote`, after line 174):

```javascript
async function flagOutput(outputId, btn) {
  if (!outputId || btn.disabled) return;
  if (!confirm("Flag this model as not a plant / failed?")) return;
  btn.disabled = true;
  try {
    const res = await fetch("/api/flag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_id: outputId, reason: "not_a_plant" }),
    });
    if (!res.ok) {
      btn.disabled = false;
      let detail = "flag not recorded";
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      setStatus("Could not flag: " + detail);
      return;
    }
    btn.textContent = "✓";
    flash("Flag recorded ✓");
  } catch (e) {
    btn.disabled = false;
    setStatus("Error flagging: " + e);
  }
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flag_client.py -v`
Expected: PASS (2 tests). Also confirm existing viewer test still green:
Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_synced_rotation.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/static/viewer.js app/static/arena.js tests/test_flag_client.py
git commit -m "feat(bad-output): /api/next output_id + ⚑ flag button wired arena→/api/flag"
```

---

### Task 6: admin Restore / Confirm-hide + moderation view

**Files:**

- Modify: `app/main.py` (`moderation_page` ~1375-1392; add two routes after `admin_reject` ~1426)
- Modify: `app/templates/moderation.html`
- Test: `tests/test_flag_admin.py`

**Interfaces:**

- Consumes: `ModelOutput.hidden_at`, `OutputFlag`, `flags.distinct_flag_count`, `_require_admin`, `storage.url_for`.
- Produces: `POST /admin/outputs/{output_id}/restore`, `POST /admin/outputs/{output_id}/hide`; `moderation_page` context gains `flagged` rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flag_admin.py
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Generator, ModelOutput, OutputFlag, Task, _utcnow

client = TestClient(app)
TOKEN = "test-token"


def _flagged_output(hidden=False):
    with SessionLocal() as db:
        g = Generator(slug=f"ad-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
        db.add(g)
        db.flush()
        t = Task(title=f"ad-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
        db.add(t)
        db.flush()
        o = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="x.glb",
            asset_format="glb",
            hidden_at=_utcnow() if hidden else None,
        )
        db.add(o)
        db.flush()
        db.add(OutputFlag(output_id=o.id, session_id="s1", reason="not_a_plant"))
        db.commit()
        return o.id


def test_hide_and_restore_round_trip():
    oid = _flagged_output(hidden=False)
    r = client.post(f"/admin/outputs/{oid}/hide", data={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(ModelOutput, oid).hidden_at is not None
    r = client.post(f"/admin/outputs/{oid}/restore", data={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(ModelOutput, oid).hidden_at is None


def test_admin_actions_require_token():
    oid = _flagged_output()
    assert client.post(f"/admin/outputs/{oid}/hide", data={"token": "wrong"}).status_code == 401


def test_moderation_page_lists_flagged():
    oid = _flagged_output(hidden=True)
    r = client.get(f"/admin/moderation?token={TOKEN}")
    assert r.status_code == 200 and str(oid) in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flag_admin.py -v`
Expected: FAIL (routes 404; page lacks the id).

- [ ] **Step 3: Extend `moderation_page` to pass flagged rows**

In `app/main.py`, `moderation_page` — before `return templates.TemplateResponse(...)`, build a `flagged` list and add it to the context:

```python
    from . import flags as _flags

    flagged_outputs = (
        db.query(ModelOutput)
        .join(OutputFlag, OutputFlag.output_id == ModelOutput.id)
        .distinct()
        .all()
    )
    flagged = []
    for o in flagged_outputs:
        flagged.append(
            {
                "id": o.id,
                "asset_url": storage.url_for(o.asset_path),
                "task": o.task.title if o.task else f"#{o.task_id}",
                "flags": _flags.distinct_flag_count(db, o.id),
                "hidden": o.hidden_at is not None,
            }
        )
    return templates.TemplateResponse(
        request, "moderation.html", {"pending": rows, "flagged": flagged}
    )
```

(Ensure `OutputFlag` is in the models import at the top of `main.py`; add it if absent.)

- [ ] **Step 4: Add the two admin routes**

After `admin_reject` (~line 1426) in `app/main.py`:

```python
@app.post("/admin/outputs/{output_id}/hide")
def admin_hide_output(
    output_id: int, token: str = Form(...), db: Session = Depends(get_db)
):
    _require_admin(token)
    out = db.get(ModelOutput, output_id)
    if out is None:
        raise HTTPException(404, "Unknown output")
    if out.hidden_at is None:
        out.hidden_at = _models_utcnow()
    db.commit()
    return RedirectResponse(f"/admin/moderation?token={quote(token)}", status_code=303)


@app.post("/admin/outputs/{output_id}/restore")
def admin_restore_output(
    output_id: int, token: str = Form(...), db: Session = Depends(get_db)
):
    _require_admin(token)
    out = db.get(ModelOutput, output_id)
    if out is None:
        raise HTTPException(404, "Unknown output")
    out.hidden_at = None
    db.commit()
    return RedirectResponse(f"/admin/moderation?token={quote(token)}", status_code=303)
```

At the top of `app/main.py`, import the timestamp helper alongside the models import:

```python
from .models import _utcnow as _models_utcnow
```

- [ ] **Step 5: Add the moderation.html section**

In `app/templates/moderation.html`, after the existing submissions block, add:

```html
<h2>Flagged / hidden outputs</h2>
{% if flagged %}
<table>
  <thead>
    <tr>
      <th>ID</th>
      <th>Task</th>
      <th>Preview</th>
      <th>Flags</th>
      <th>State</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {% for f in flagged %}
    <tr>
      <td>{{ f.id }}</td>
      <td>{{ f.task }}</td>
      <td><a href="{{ f.asset_url }}" target="_blank">asset</a></td>
      <td>{{ f.flags }}</td>
      <td>{{ "hidden" if f.hidden else "visible" }}</td>
      <td>
        {% if f.hidden %}
        <form method="post" action="/admin/outputs/{{ f.id }}/restore">
          <input
            type="hidden"
            name="token"
            value="{{ request.query_params.get('token', '') }}"
          />
          <button type="submit">Restore</button>
        </form>
        {% else %}
        <form method="post" action="/admin/outputs/{{ f.id }}/hide">
          <input
            type="hidden"
            name="token"
            value="{{ request.query_params.get('token', '') }}"
          />
          <button type="submit">Confirm-hide</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p>No flagged outputs.</p>
{% endif %}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest tests/test_flag_admin.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the full suite**

Run: `PYTHONPATH="$(pwd)" .venv/bin/pytest -q`
Expected: PASS — 642 baseline + new tests, 8 skipped, 0 failed.

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/templates/moderation.html tests/test_flag_admin.py
git commit -m "feat(bad-output): admin Restore/Confirm-hide + flagged-outputs moderation view"
```

---

## Self-Review

**Spec coverage:**

- Auto-gate on `{isolated-organ, fragment}` + keep complete/partial/unscored → Task 2 (`excluded_output_ids_by_completeness`) + Task 3 (wiring). ✅
- `hidden_at` pool exclusion → Task 1 (column) + Task 3 (wiring). ✅
- `OutputFlag` table + distinct-session dedup + auto-hide at K → Task 1/2/4. ✅
- `POST /api/flag` rate-limited + 404/422 → Task 4. ✅
- `output_id` in `/api/next` (no generator leak) → Task 5. ✅
- ⚑ button in control bar wired arena→/api/flag, decoupled from voting, non-silent failure → Task 5. ✅
- Admin Restore/Confirm-hide + moderation listing, token-gated → Task 6. ✅
- Config tunables (categories, K) → Task 1. ✅
- Failure-label dataset = `OutputFlag` rows (queryable, no export UI) → satisfied by Task 1 schema; non-goal for UI. ✅
- pick_task/pick_pair parity (no 404 regression) → Task 3 Step 4 re-runs matchmaking tests. ✅

**Placeholder scan:** none — every code step has full code.

**Type consistency:** `record_flag(db, output_id, session_id, reason, threshold) -> (bool, int)` used identically in Task 2, 4. `excluded_output_ids_by_completeness(db, categories)` consistent Task 2/3. `mount(slot, asset, onFlag)` / `addControls(slot, onFlag)` consistent across viewer.js edits and arena.js call. `/api/flag` request body `{output_id, reason}` matches `FlagIn`. Payload `data.a.output_id` matches `_serialize`. ✅

**Note for implementer:** Task 3's test asserts on DB truth via `Comparison` (not the payload shape), so it is green before Task 5 adds `output_id` to `/api/next`. Drop the unused `TestClient`/`config` imports the template carries if your linter flags them.
