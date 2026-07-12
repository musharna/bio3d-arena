# Leaderboard Clarity IA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the leaderboard around a modality hub — 4 clearly-labeled human-vote boards, AI-judge delineated on its own page, a model-detail head-to-head matrix, votes-until-firm status — without changing ranking math.

**Architecture:** Presentation + two read-only aggregations layered on existing services. `capture_scan` joins the app-hidden paradigms. `/leaderboard` becomes a modality **hub** (cards); each modality drills into the existing single-paradigm board via a clean `/leaderboard/<modality>` path route (which reuses the current `?paradigm=` rendering). Head-to-head is aggregated from `service._matches_for_scope`. No schema changes, no migrations.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Jinja2, vanilla JS, pytest. Python 3.13, `.venv` at repo root.

## Global Constraints

- **Within-paradigm ranking invariant:** every board ranks exactly one paradigm value; never present a cross-paradigm BT score as comparable. (Removing the "Overall" cross-paradigm ranking enforces this.)
- **App-hidden paradigms never surface:** honor `config.APP_HIDDEN_PARADIGMS` + `service.mode_a_excluded_generator_ids`. After Task 1 this set is `{"retrieval", "procedural_expert", "capture_scan"}`.
- **Visible modality boards = exactly 4:** `image_recon`, `text_native`, `procedural_llm`, `agentic`.
- **Modality names come from `paradigms.DISPLAY_NAMES` / `SHORT_NAMES`** — never hard-code new strings. One-liners live in the new `paradigms.WHAT_THIS_MEASURES`.
- **Honest sparse state:** a board/card backed by `< service.FIRM_VOTE_THRESHOLD` votes reads as evaluation-in-progress (votes-until-firm), never as broken/settled.
- **No new ranking math / no schema change / no migration.** Reuse `_matches_for_scope`, `_leaderboard_rows`, `service.finalize_rows`, `cached_kingdom_leaderboard_rows`, `FIRM_VOTE_THRESHOLD`.
- **Test DB safety:** never run pytest with `BIO3D_DATABASE_URL`/`BIO3D_DB_PATH` set to a real DB. Run tests as: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest ...` (conftest isolates into a temp dir).
- **Full-suite command (≈3 min):** `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest -q`.

## File Structure

- `app/config.py` — add `capture_scan` to `APP_HIDDEN_PARADIGMS` (Task 1).
- `app/paradigms.py` — add `WHAT_THIS_MEASURES` map (Task 2).
- `app/service.py` — add `head_to_head_record()` (Task 3), `firm_status()` (Task 4), `modality_hub_cards()` (Task 5).
- `app/main.py` — hub-ify `/leaderboard` (Task 5), add `/leaderboard/{modality}` (Task 6), enrich `model_detail` (Task 8), delineate `/leaderboard/judge` (Task 9).
- `app/templates/leaderboard_hub.html` — NEW hub (Task 7).
- `app/templates/leaderboard.html` — board view: add status column, judge link, explainer (Task 7).
- `app/templates/model_detail.html` — head-to-head section (Task 8).
- `app/templates/_leaderboard_judge.html` / `leaderboard_judge` — title + explanation + modality selector (Task 9).
- `app/static/style.css` — hub card grid + head-to-head + status styles, responsive (Tasks 7, 8).
- Tests: `tests/test_hidden_paradigms.py` (edit), `tests/test_paradigm_copy.py` (new), `tests/test_head_to_head.py` (new), `tests/test_firm_status.py` (new), `tests/test_leaderboard_hub.py` (new), `tests/test_modality_board_route.py` (new), `tests/test_model_detail_h2h.py` (new), `tests/test_judge_delineation.py` (new).

---

### Task 1: Move `capture_scan` to app-hidden

**Files:**

- Modify: `app/config.py:191`
- Test: `tests/test_hidden_paradigms.py`

**Interfaces:**

- Produces: `config.APP_HIDDEN_PARADIGMS == frozenset({"retrieval", "procedural_expert", "capture_scan"})`.

- [ ] **Step 1: Update the failing test** — in `tests/test_hidden_paradigms.py::test_hidden_paradigms_configured`, add:

```python
    assert "capture_scan" in config.APP_HIDDEN_PARADIGMS  # data-capture reference, internal-only
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_hidden_paradigms.py::test_hidden_paradigms_configured -v`
Expected: FAIL (`capture_scan` not in the set).

- [ ] **Step 3: Implement** — `app/config.py:191`:

```python
APP_HIDDEN_PARADIGMS = frozenset({"retrieval", "procedural_expert", "capture_scan"})
```

Update the preceding comment block to add a `capture_scan —` bullet ("photogrammetry / real-world capture; a data-capture reference, thin, kept for internal analysis").

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_hidden_paradigms.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_hidden_paradigms.py
git commit -m "feat(leaderboard): hide capture_scan paradigm (internal-only)"
```

---

### Task 2: `WHAT_THIS_MEASURES` copy map

**Files:**

- Modify: `app/paradigms.py` (after `SHORT_NAMES`)
- Test: `tests/test_paradigm_copy.py` (new)

**Interfaces:**

- Produces: `paradigms.WHAT_THIS_MEASURES: dict[str, str]` — one plain-language sentence per non-hidden paradigm.

- [ ] **Step 1: Write the failing test** — `tests/test_paradigm_copy.py`:

```python
from app import config, paradigms


def test_what_this_measures_covers_every_visible_paradigm():
    visible = [p for p in paradigms.PARADIGMS if p not in config.APP_HIDDEN_PARADIGMS]
    for p in visible:
        assert p in paradigms.WHAT_THIS_MEASURES, f"missing one-liner for {p}"
        assert paradigms.WHAT_THIS_MEASURES[p].strip()
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_paradigm_copy.py -v`
Expected: FAIL (`AttributeError: WHAT_THIS_MEASURES`).

- [ ] **Step 3: Implement** — in `app/paradigms.py` after `SHORT_NAMES`:

```python
# One plain-language line per modality for the leaderboard hub cards / board headers.
WHAT_THIS_MEASURES: dict[str, str] = {
    "image_recon": "A single photo reconstructed into a 3D mesh.",
    "text_native": "A text prompt turned directly into 3D.",
    "procedural_llm": "An LLM writes Blender/CAD code that builds the 3D model.",
    "agentic": "An agent renders, critiques, and revises its own 3D model in a loop.",
    "capture_scan": "A real organism captured to 3D by photogrammetry / scanning.",
    "video": "Video frames lifted into 3D / 4D.",
    "texturing": "Editing or texturing an existing 3D model.",
    "retrieval": "A pre-existing human-made asset retrieved from a library.",
    "procedural_expert": "Hand-authored rule-based / simulation generators.",
    "sketch": "A sketch turned into 3D.",
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_paradigm_copy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/paradigms.py tests/test_paradigm_copy.py
git commit -m "feat(leaderboard): what-this-measures copy per modality"
```

---

### Task 3: Head-to-head record aggregation (#74 backend)

**Files:**

- Modify: `app/service.py` (new function near `_matches_for_scope`)
- Test: `tests/test_head_to_head.py` (new)

**Interfaces:**

- Consumes: `service._matches_for_scope(db, criterion_id, category_id=None, *, category_ids=None) -> (list[(winner_gen, loser_gen)], list[int])`.
- Produces:
  `service.head_to_head_record(db, generator_id: int, criterion_slug: str = "overall", *, category_ids: set[int] | None = None) -> list[dict]`
  returning, per opponent generator the target has ≥1 decisive game with:
  `{"opponent_id": int, "wins": int, "losses": int, "games": int, "win_pct": float}` (win_pct in 0..1, sorted by games desc then win_pct desc). Ties count as a half-win + half-loss (already split by `_matches_for_scope`). Empty list when the target has no decisive games. Opponents are same-paradigm by construction (`_matches_for_scope` filters cross-paradigm out).

- [ ] **Step 1: Write the failing test** — `tests/test_head_to_head.py`:

```python
from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category, Comparison, Criterion, Generator, ModelOutput, Task, Vote,
)


def setup_module(_m):
    init_db()


def _mk(db, slug, paradigm="image_recon"):
    g = Generator(slug=slug, name=slug, paradigm=paradigm)
    db.add(g); db.flush()
    return g


def test_head_to_head_counts_wins_losses_and_pct():
    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(slug="overall", name="Overall")
        db.add(crit); db.flush()
        cat = Category(slug="h2h-cat", name="H"); db.add(cat); db.flush()
        task = Task(category_id=cat.id, title="h2h-task", prompt="p"); db.add(task); db.flush()
        a, b = _mk(db, "h2h-a"), _mk(db, "h2h-b")
        oa = ModelOutput(task_id=task.id, generator_id=a.id, asset_path="a.glb", asset_format="glb")
        ob = ModelOutput(task_id=task.id, generator_id=b.id, asset_path="b.glb", asset_format="glb")
        db.add_all([oa, ob]); db.flush()
        # 3 comparisons A vs B: A wins twice, B wins once.
        for winner in ("a", "a", "b"):
            c = Comparison(task_id=task.id, criterion_id=crit.id,
                           output_a_id=oa.id, output_b_id=ob.id, is_gold=False)
            db.add(c); db.flush()
            db.add(Vote(comparison_id=c.id, winner=winner, session_id="h2h-sess"))
        db.commit()

        rec = service.head_to_head_record(db, a.id, "overall")
        assert len(rec) == 1
        row = rec[0]
        assert row["opponent_id"] == b.id
        assert row["wins"] == 2 and row["losses"] == 1 and row["games"] == 3
        assert abs(row["win_pct"] - 2 / 3) < 1e-6


def test_head_to_head_empty_when_no_games():
    with SessionLocal() as db:
        g = _mk(db, "h2h-lonely"); db.commit()
        assert service.head_to_head_record(db, g.id, "overall") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_head_to_head.py -v`
Expected: FAIL (`AttributeError: head_to_head_record`).

- [ ] **Step 3: Implement** — in `app/service.py`, after `_matches_for_scope`:

```python
def head_to_head_record(
    db: Session,
    generator_id: int,
    criterion_slug: str = "overall",
    *,
    category_ids: set[int] | None = None,
) -> list[dict]:
    """Per-opponent decisive win/loss record for one generator within its paradigm scope.

    Built from _matches_for_scope (already same-paradigm, gold/reference-excluded, trust-gated,
    ties split). Returns [] when the generator has no decisive games. Sorted games desc, win% desc.
    """
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    matches, _groups = _matches_for_scope(db, crit.id, category_ids=category_ids)
    tally: dict[int, dict] = {}
    for winner, loser in matches:
        if winner == generator_id:
            t = tally.setdefault(loser, {"wins": 0, "losses": 0})
            t["wins"] += 1
        elif loser == generator_id:
            t = tally.setdefault(winner, {"wins": 0, "losses": 0})
            t["losses"] += 1
    out = []
    for opp, t in tally.items():
        games = t["wins"] + t["losses"]
        out.append({
            "opponent_id": opp,
            "wins": t["wins"],
            "losses": t["losses"],
            "games": games,
            "win_pct": (t["wins"] / games) if games else 0.0,
        })
    out.sort(key=lambda r: (r["games"], r["win_pct"]), reverse=True)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_head_to_head.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/service.py tests/test_head_to_head.py
git commit -m "feat(leaderboard): head-to-head record aggregation"
```

---

### Task 4: `firm_status` votes-until-firm helper (#76)

**Files:**

- Modify: `app/service.py` (near `FIRM_VOTE_THRESHOLD`, ~line 1241)
- Test: `tests/test_firm_status.py` (new)

**Interfaces:**

- Produces: `service.firm_status(n_games: int) -> dict` = `{"firm": bool, "label": str}` — `{"firm": True, "label": "firm"}` at/above `FIRM_VOTE_THRESHOLD`, else `{"firm": False, "label": "{remaining} more votes → firm"}`.

- [ ] **Step 1: Write the failing test** — `tests/test_firm_status.py`:

```python
from app import service


def test_firm_status_firm_at_threshold():
    n = service.FIRM_VOTE_THRESHOLD
    assert service.firm_status(n) == {"firm": True, "label": "firm"}
    assert service.firm_status(n + 5)["firm"] is True


def test_firm_status_counts_remaining_below_threshold():
    n = service.FIRM_VOTE_THRESHOLD - 3
    s = service.firm_status(n)
    assert s["firm"] is False
    assert s["label"] == "3 more votes → firm"
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_firm_status.py -v`
Expected: FAIL (`AttributeError: firm_status`).

- [ ] **Step 3: Implement** — in `app/service.py` just after the `FIRM_VOTE_THRESHOLD` definition:

```python
def firm_status(n_games: int) -> dict:
    """Votes-until-firm signal for a leaderboard row. `firm` once n_games >= FIRM_VOTE_THRESHOLD,
    else a countdown label so a low-vote rank reads as evaluation-in-progress, not settled."""
    if n_games >= FIRM_VOTE_THRESHOLD:
        return {"firm": True, "label": "firm"}
    return {"firm": False, "label": f"{FIRM_VOTE_THRESHOLD - n_games} more votes → firm"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_firm_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/service.py tests/test_firm_status.py
git commit -m "feat(leaderboard): firm_status votes-until-firm helper"
```

---

### Task 5: Modality hub data + `/leaderboard` becomes the hub

**Files:**

- Modify: `app/service.py` (new `modality_hub_cards`)
- Modify: `app/main.py:1199-1350` (leaderboard route — hub default; drop Overall)
- Create: `app/templates/leaderboard_hub.html` (minimal; full styling in Task 7)
- Test: `tests/test_leaderboard_hub.py` (new)

**Interfaces:**

- Consumes: `service._leaderboard_rows`/`_leaderboard_rows` helper in main.py, `paradigms.DISPLAY_NAMES`, `paradigms.WHAT_THIS_MEASURES`, `config.APP_HIDDEN_PARADIGMS`, `service.firm_status`.
- Produces: `service.modality_hub_cards(db, criterion, kingdom, cat_ids) -> list[dict]`, each:
  `{"paradigm": str, "display": str, "what": str, "top": list[dict], "model_count": int, "firm": bool}` where `top` is up to 3 finalized rows (rank/name/bt_score). Order follows `paradigms.PARADIGMS`; only non-hidden paradigms with ≥1 rated entrant in scope. `firm` is True iff any row's `n_games >= FIRM_VOTE_THRESHOLD`.

- [ ] **Step 1: Write the failing route test** — `tests/test_leaderboard_hub.py`:

```python
from fastapi.testclient import TestClient
from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_m):
    seed_all(force=True)


def test_hub_shows_only_visible_modalities():
    html = client.get("/leaderboard").text
    # Hub landing renders modality cards, not the old stacked "By paradigm" tab default.
    assert 'class="lb-hub"' in html
    for hidden in ("Retrieved asset", "Expert / simulation procedural", "Scan / capture"):
        assert hidden not in html
    # No cross-paradigm Overall ranking on the landing.
    assert "cross-paradigm" not in html.lower() or "aren't comparable" in html.lower()


def test_hub_card_links_to_modality_board():
    html = client.get("/leaderboard").text
    assert "/leaderboard/image_recon" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_leaderboard_hub.py -v`
Expected: FAIL (no `lb-hub`, old stacked template).

- [ ] **Step 3a: Implement `modality_hub_cards`** in `app/service.py`:

```python
def modality_hub_cards(db, criterion: str, cat_ids, category_id_sel, rows_fn) -> list[dict]:
    """One hub card per visible modality. rows_fn(paradigm) -> finalized+enriched rows for that
    paradigm in the current scope (main.py passes a closure over _leaderboard_rows/_enrich)."""
    cards = []
    for p in paradigms.PARADIGMS:
        if p in config.APP_HIDDEN_PARADIGMS:
            continue
        rows = rows_fn(p)
        rated = [r for r in rows if r.get("n_games", 0) > 0]
        if not rated:
            continue
        top = rated[:3]
        cards.append({
            "paradigm": p,
            "display": paradigms.DISPLAY_NAMES.get(p, p),
            "what": paradigms.WHAT_THIS_MEASURES.get(p, ""),
            "top": top,
            "model_count": len(rated),
            "firm": any(r.get("n_games", 0) >= FIRM_VOTE_THRESHOLD for r in rated),
        })
    return cards
```

- [ ] **Step 3b: Rewire `/leaderboard`** (`app/main.py`): when `not verified and paradigm is None and not overall` (the old `stacked` default), build `cards = service.modality_hub_cards(...)` with a `rows_fn` closure that calls the existing `_leaderboard_rows(db, criterion, category, p, kingdom)` then `_finish(...)`, and render `leaderboard_hub.html` with `{cards, kingdom filter context, sel_criterion, sel_category}`. Remove the `{"mode": "overall", ...}` entry from `paradigm_options` and delete the `overall` board branch/`board_title` for the cross-paradigm merged view. Keep `?paradigm=X` working (it now also has its own path route, Task 6) and keep `verified` handling.

- [ ] **Step 3c: Minimal `leaderboard_hub.html`** (styling in Task 7) — enough to pass tests:

```html
{% extends "base.html" %} {% block content %}
<section class="lb-hub">
  <h1>Leaderboard {% include "_scope_pill.html" %}</h1>
  <p class="subtle">
    Each method is ranked on its own — scores aren't comparable across methods
    (separate match pools). Pick a method to compare within it.
  </p>
  <div class="lb-hub-grid">
    {% for c in cards %}
    <a
      class="lb-hub-card"
      href="/leaderboard/{{ c.paradigm }}?criterion={{ sel_criterion }}&category={{ sel_category }}"
    >
      <h3>{{ c.display }}</h3>
      <p class="lb-hub-what">{{ c.what }}</p>
      <ol class="lb-hub-top">
        {% for r in c.top %}
        <li>
          {{ loop.index }} {{ r.generator }}
          <span>{{ r.bt_score|round|int }}</span>
        </li>
        {% endfor %}
      </ol>
      <p class="lb-hub-pop">
        {{ c.model_count }} model{{ 's' if c.model_count != 1 }} · {{ 'firm' if
        c.firm else 'provisional' }}
      </p>
      <span class="lb-hub-cta">view board →</span>
    </a>
    {% endfor %}
  </div>
</section>
{% endblock %}
```

(Confirm the row key for the generator name — reuse whatever `_enrich_leaderboard_rows` sets, i.e. `r["generator"]` / `r["bt_score"]`; read `leaderboard.html` lines 17-69 for the exact keys and mirror them.)

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_leaderboard_hub.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/service.py app/main.py app/templates/leaderboard_hub.html tests/test_leaderboard_hub.py
git commit -m "feat(leaderboard): modality hub landing; drop cross-paradigm Overall"
```

---

### Task 6: `/leaderboard/{modality}` board route + votes-until-firm status

**Files:**

- Modify: `app/main.py` (new path route)
- Modify: `app/templates/leaderboard.html` (status column + judge link + what-measures header)
- Test: `tests/test_modality_board_route.py` (new)

**Interfaces:**

- Consumes: existing single-paradigm rendering (the `?paradigm=` branch), `service.firm_status`, `paradigms.WHAT_THIS_MEASURES`.
- Produces: `GET /leaderboard/{modality}` → the single-paradigm board; 404 for unknown or app-hidden paradigm. Each rendered row carries `status` from `service.firm_status(r["n_games"])`.

- [ ] **Step 1: Write the failing test** — `tests/test_modality_board_route.py`:

```python
from fastapi.testclient import TestClient
from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_m):
    seed_all(force=True)


def test_modality_board_renders_single_paradigm():
    r = client.get("/leaderboard/image_recon")
    assert r.status_code == 200
    assert "A single photo reconstructed" in r.text  # what-this-measures header
    assert "see the AI-judge board" in r.text


def test_hidden_or_unknown_modality_is_404():
    assert client.get("/leaderboard/capture_scan").status_code == 404
    assert client.get("/leaderboard/retrieval").status_code == 404
    assert client.get("/leaderboard/not_a_paradigm").status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_modality_board_route.py -v`
Expected: FAIL (route 404s for all, or no what-measures header).

- [ ] **Step 3a: Add the route** in `app/main.py` (place ABOVE `/leaderboard/judge` so the static path isn't shadowed; FastAPI matches `/leaderboard/judge` on its own decorator regardless, but keep modality validation strict):

```python
@app.get("/leaderboard/{modality}", response_class=HTMLResponse)
def leaderboard_modality(
    modality: str,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    verified: bool = False,
    show_all: bool = False,
):
    if modality in config.APP_HIDDEN_PARADIGMS or not paradigms.is_valid_paradigm(modality):
        raise HTTPException(404, "Unknown modality")
    # Reuse the single-paradigm board by delegating to the existing leaderboard handler.
    return leaderboard(
        request, db, criterion=criterion, category=category,
        paradigm=modality, verified=verified, show_all=show_all, overall=False,
    )
```

- [ ] **Step 3b: Board header + status** in `app/templates/leaderboard.html`: when a single `paradigm` is selected, render the `WHAT_THIS_MEASURES` line under the title, a "Ranked by human votes · N cast · → see the AI-judge board" line linking `/leaderboard/judge?...&modality={{ sel_paradigm_value }}`, and add a `status` cell per row using `firm_status`. Pass a `status_by_gid` map from the route, OR compute in-template via a new `firm_status` Jinja global. Simplest: in `_finish`/`_enrich_leaderboard_rows` add `r["status"] = service.firm_status(r.get("n_games", 0))` so the template reads `r.status.label` / `r.status.firm`. Add the column to the `<table>` header + body in `leaderboard.html:17-69`.

- [ ] **Step 3c:** register `paradigms` + `service.firm_status` availability wherever the template needs them (the route already passes `firm_vote_threshold`; enrich rows with `status` in Python per 3b to avoid template logic).

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_modality_board_route.py tests/test_leaderboard.py -v`
Expected: PASS (new route + existing leaderboard tests still green).

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/templates/leaderboard.html
git commit -m "feat(leaderboard): /leaderboard/<modality> board + votes-until-firm status"
```

---

### Task 7: Hub + board styling (CSS, responsive) + variant seam

**Files:**

- Modify: `app/static/style.css`
- Modify: `app/templates/leaderboard_hub.html`, `app/templates/leaderboard.html`
- Test: `tests/test_leaderboard_hub.py` (extend — served-CSS guard)

**Interfaces:**

- Consumes: `.lb-hub`, `.lb-hub-grid`, `.lb-hub-card`, `.lb-hub-*` from Task 5; existing `.ranktable` styles.
- Produces: responsive hub card grid; a row-render include that accepts an optional `variant_of`/children grouping (data-shape seam only — no variant UI).

- [ ] **Step 1: Extend the test** — in `tests/test_leaderboard_hub.py`:

```python
def test_hub_grid_is_styled_and_responsive():
    css = client.get("/static/style.css").text
    assert ".lb-hub-grid" in css
    assert "@media" in css  # responsive rules exist for the hub
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_leaderboard_hub.py::test_hub_grid_is_styled_and_responsive -v`
Expected: FAIL (`.lb-hub-grid` not in CSS).

- [ ] **Step 3: Implement CSS** — add to `app/static/style.css` (match existing token vars / card styles; grep `.b3d-model-card` for the card pattern to mirror):

```css
.lb-hub-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 1rem;
}
.lb-hub-card {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  text-decoration: none;
}
.lb-hub-card:hover {
  border-color: var(--accent);
}
.lb-hub-what {
  color: var(--muted);
  font-size: 0.85rem;
}
.lb-hub-top {
  list-style: none;
  margin: 0;
  padding: 0;
  font-variant-numeric: tabular-nums;
}
.lb-hub-pop {
  font-size: 0.8rem;
  color: var(--muted);
}
.lb-hub-cta {
  color: var(--accent);
  font-weight: 600;
}
@media (max-width: 640px) {
  .lb-hub-grid {
    grid-template-columns: 1fr;
  }
}
```

Use the actual token variable names from the top of `style.css` (grep `--surface`/`--border`/`--accent`/`--muted`/`--radius`; substitute the real names). Add the votes-until-firm `status` cell styling (a small muted pill; `firm` gets the accent, provisional gets muted).

- [ ] **Step 3b: Variant seam** — factor the board's per-row `<tr>` into a `{% macro lb_row(r) %}` in `leaderboard.html` that already tolerates an optional `r.variant_of` (indent child rows) and optional `r.children` (rendered recursively) — but pass none yet. This is a data-shape seam; no new behavior. A code comment must state Spec 2 fills it.

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_leaderboard_hub.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/static/style.css app/templates/leaderboard_hub.html app/templates/leaderboard.html tests/test_leaderboard_hub.py
git commit -m "feat(leaderboard): hub card grid styling (responsive) + variant row seam"
```

---

### Task 8: Model-detail head-to-head matrix (#74 UI)

**Files:**

- Modify: `app/main.py:1474-1530` (model_detail route)
- Modify: `app/templates/model_detail.html`
- Test: `tests/test_model_detail_h2h.py` (new)

**Interfaces:**

- Consumes: `service.head_to_head_record(db, gen.id, "overall", category_ids=k_ids)`, `service.generator_display_names(db)`.
- Produces: model_detail context gets `h2h: list[dict]` where each row adds `opponent_name` to the `head_to_head_record` dict. Template renders a "Head-to-head (within its method)" section; empty → "Not enough head-to-head data yet."

- [ ] **Step 1: Write the failing test** — `tests/test_model_detail_h2h.py`:

```python
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Generator
from app.seed import seed_all

client = TestClient(app)


def setup_module(_m):
    seed_all(force=True)


def test_model_detail_has_head_to_head_section():
    with SessionLocal() as db:
        slug = db.query(Generator).filter(Generator.paradigm == "image_recon").first().slug
    html = client.get(f"/models/{slug}").text
    assert "Head-to-head" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_model_detail_h2h.py -v`
Expected: FAIL (no "Head-to-head").

- [ ] **Step 3a: Route** — in `app/main.py` `model_detail`, before the `TemplateResponse`:

```python
    names = service.generator_display_names(db)
    h2h = [
        {**rec, "opponent_name": names.get(rec["opponent_id"], "Unknown")}
        for rec in service.head_to_head_record(db, gen.id, "overall", category_ids=k_ids)
    ]
```

Add `"h2h": h2h` to the template context dict.

- [ ] **Step 3b: Template** — in `app/templates/model_detail.html`, add a section:

```html
<section class="md-h2h">
  <h2>Head-to-head <span class="subtle">(within its method)</span></h2>
  {% if h2h %}
  <table class="ranktable">
    <thead>
      <tr>
        <th>opponent</th>
        <th>record</th>
        <th>win rate</th>
      </tr>
    </thead>
    <tbody>
      {% for r in h2h %}
      <tr>
        <td>{{ r.opponent_name }}</td>
        <td>{{ r.wins }}–{{ r.losses }} (n={{ r.games }})</td>
        <td>{{ (r.win_pct * 100)|round|int }}%</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="subtle">Not enough head-to-head data yet.</p>
  {% endif %}
</section>
```

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_model_detail_h2h.py tests/test_models_page.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/templates/model_detail.html tests/test_model_detail_h2h.py
git commit -m "feat(models): head-to-head matrix on model detail"
```

---

### Task 9: AI-judge page delineation

**Files:**

- Modify: `app/main.py` `leaderboard_judge` (~1353) — accept `modality`, pass explanation context.
- Modify: `app/templates/_leaderboard_judge.html` — title + "these ranks come from a VLM judge, not human votes" + modality selector mirroring the 4 boards.
- Test: `tests/test_judge_delineation.py` (new)

**Interfaces:**

- Consumes: `paradigms.DISPLAY_NAMES`, `config.APP_HIDDEN_PARADIGMS`.
- Produces: `/leaderboard/judge` fragment carries the explanation string and links back to each human modality board.

- [ ] **Step 1: Write the failing test** — `tests/test_judge_delineation.py`:

```python
from fastapi.testclient import TestClient
from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_m):
    seed_all(force=True)


def test_judge_fragment_is_labeled_as_vlm_not_human():
    html = client.get("/leaderboard/judge").text
    assert "VLM judge" in html or "not human votes" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_judge_delineation.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `app/templates/_leaderboard_judge.html` add a header line: `<p class="subtle">These ranks come from a VLM judge, not human votes.</p>` and, if `sel_paradigm`/modality context is available, a small "human board →" backlink. Keep the existing table. (Read the current `_leaderboard_judge.html` first to place the line above the table.)

- [ ] **Step 4: Run to verify it passes**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_judge_delineation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/templates/_leaderboard_judge.html tests/test_judge_delineation.py
git commit -m "feat(leaderboard): delineate the AI-judge board (VLM, not human votes)"
```

---

### Task 10: Regression — no cross-paradigm Overall, full suite

**Files:**

- Test: `tests/test_leaderboard_hub.py` (extend)

**Interfaces:**

- Consumes: everything above.

- [ ] **Step 1: Write the failing/guard test** — extend `tests/test_leaderboard_hub.py`:

```python
def test_no_cross_paradigm_overall_ranking():
    # The caveated cross-paradigm merged ranking is removed from the primary flow.
    html = client.get("/leaderboard").text
    assert "Overall — all methods" not in html
    # The old ?overall=true board must no longer render a combined numeric ranking table.
    over = client.get("/leaderboard?overall=true")
    assert "Overall — all methods" not in over.text
```

- [ ] **Step 2: Run to verify it fails/passes appropriately**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest tests/test_leaderboard_hub.py -v`
Expected: PASS after Task 5 removed the Overall board (if FAIL, finish removing the `overall` branch / `board_title`).

- [ ] **Step 3: Run the FULL suite**

Run: `env -u BIO3D_DATABASE_URL -u BIO3D_DB_PATH -u BIO3D_DATA_DIR .venv/bin/python -m pytest -q`
Expected: all pass (fix any leaderboard.html tests that asserted the old tab/Overall structure — update them to the hub/board model; these are legitimate behavior-change updates, not deletions of coverage).

- [ ] **Step 4: Manual smoke (optional)** — start the server, load `/leaderboard` (hub), click into `/leaderboard/image_recon`, open a model page, expand the judge board; confirm 200s and correct copy.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test(leaderboard): regression guards for hub IA + Overall removal"
```

---

## Self-Review

**Spec coverage:** hub (T5,T7) · per-modality board (T6) · human-primary + judge separate (T6 link, T9) · Overall removed (T5,T10) · kingdom global + criterion in-board (T5,T6 reuse existing scope) · capture_scan hidden (T1) · WHAT_THIS_MEASURES (T2) · head-to-head #74 (T3,T8) · votes-until-firm #76 (T4,T6) · variant seam (T7) · responsive (T7). All spec sections map to a task.

**Placeholder scan:** template Tasks (5b, 6b, 7, 9) intentionally instruct the implementer to read the exact current template/CSS token names and mirror them — this is grounding, not a placeholder, because the concrete structure + the keys to read are named. All logic tasks carry complete code.

**Type consistency:** `head_to_head_record` returns dicts with `opponent_id/wins/losses/games/win_pct` (T3) → T8 adds `opponent_name` and reads exactly those. `firm_status` returns `{firm,label}` (T4) → T6 reads `.label`/`.firm`. `modality_hub_cards` returns `{paradigm,display,what,top,model_count,firm}` (T5) → hub template reads exactly those.
