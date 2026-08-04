# Taxon3D — Increment 2: Leaderboard Credibility Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the leaderboard read as statistically credible — CI-grouped "Rank (Upper Bound)" so models with overlapping 95% CIs share a rank, a CI whisker bar so ties are obvious at a glance, and a locked-in regression test + methodology note confirming Bradley–Terry tie handling.

**Architecture:** Pure ranking logic lives in `app/ranking.py` (no DB), wired into the existing `_leaderboard_rows` serializer in `app/main.py`; presentation in `app/templates/leaderboard.html` + `app/static/style.css`. No schema change — `Rating.bt_lower/bt_upper/n_games` already exist and are populated by `service.recompute_scope`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Jinja2, vanilla JS/CSS, pytest.

## Grounding facts (verified against live source 2026-06-20 — do NOT re-derive, but DO confirm before editing)

- **Tie handling is ALREADY correct.** `service._matches_for_scope` (service.py:105-107) credits each `tie` as a split — one win in each direction — feeding Bradley–Terry symmetrically; `apply_vote` (service.py:62) scores a tie as Elo 0.5. **Ties are NOT silently dropped.** This increment ADDS A REGRESSION TEST locking this in; it does NOT change the fit. (Audit B1's "verify ties aren't dropped" → verified: handled.)
- **CIs + game counts already exist and are surfaced as TEXT.** `Rating.bt_lower/bt_upper/n_games` are populated in `service.recompute_scope` (service.py:130-133); `leaderboard.html:46-48` already renders `[lower, upper]` and a Games column. Keep these. The new work is the _bar_ + the _rank logic_.
- **Rank is currently plain sequential point order.** `_leaderboard_rows` (main.py:280-282) sorts by `bt_score` desc and assigns `rank = 1,2,3,…`. This increment replaces that with CI-grouped rank.
- `_leaderboard_rows` returns dicts with keys: `generator, kind, elo, bt_score, bt_lower, bt_upper, n_games, rank` (values rounded to 1 decimal except n_games/rank). Both `/leaderboard` (HTML) and `/api/leaderboard` (JSON) call it.

## Global Constraints

- **Test runner:** `.venv/bin/python -m pytest` (base conda lacks sqlalchemy). `ruff check app/ tests/` must pass; ruff auto-fix strips imports added before first use — add import + usage in one edit, re-grep.
- **Templates:** NO `==` in Jinja (`app/templates/` is in `.prettierignore`; prettier mangles it). Precompute booleans/values in Python; the existing template uses `{% if opt.selected %}` style — follow it.
- **CI-grouped rank formula (exact):** `rank_i = 1 + #{ j : lower_j > upper_i }` — a model j outranks i only when j's 95% lower bound is strictly greater than i's 95% upper bound. Overlapping CIs ⇒ shared rank (mirrors LMArena "Rank (UB)").
- **Display order stays by point estimate** (`bt_score` desc); only the rank NUMBER changes (consecutive rows may share a rank, e.g. 1, 1, 3).
- **No schema migration** — use existing `Rating` fields.
- **Commits** end with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW`
- Do NOT merge to master — the controller runs the final whole-branch review then merges.
- After the increment, flip the matching `[ ]→[x]` checkboxes in `docs/audits/2026-06-20-field-audit.md` (B1 CI-rank, per-row CI, tie handling).

## File Structure

- Modify `app/ranking.py` — add pure `rank_by_ci(bounds) -> list[int]`.
- Modify `app/main.py` `_leaderboard_rows` (253-283) — use `rank_by_ci`; add CI-bar geometry fields.
- Modify `app/templates/leaderboard.html` — "Rank (UB)" header + shared-rank display + CI whisker bar.
- Modify `app/static/style.css` — `.ci-bar` / `.ci-range` / `.ci-dot` styles.
- Modify `app/templates/methodology.html` — note on CI-grouped rank + tie handling (read it first to match structure).
- Tests: `tests/test_ranking.py` (extend — pure fn), `tests/test_research.py` or a new `tests/test_leaderboard.py` (route-level shared-rank + CI-bar + tie regression).

---

### Task 1: CI-grouped rank logic (pure fn + wire into the serializer)

**Files:**

- Modify: `app/ranking.py` (add `rank_by_ci` after `bradley_terry`)
- Modify: `app/main.py:253-283` (`_leaderboard_rows`)
- Test: `tests/test_ranking.py` (extend), `tests/test_leaderboard.py` (create)

**Interfaces:**

- Produces: `ranking.rank_by_ci(bounds: list[tuple[float, float]]) -> list[int]` — `bounds[i] = (lower_i, upper_i)`, returns rank per i in the same order.
- `_leaderboard_rows` rows gain `ci_left/ci_width/ci_point` (floats, percent 0–100) and a CI-grouped `rank`.

- [ ] **Step 1: Write the failing pure-fn test (append to `tests/test_ranking.py`)**

```python
def test_rank_by_ci_groups_overlapping_intervals():
    from app.ranking import rank_by_ci

    # A clearly ahead (CI above all); B and C overlap each other; D clearly last.
    #         A            B            C            D
    bounds = [(1200, 1300), (1000, 1100), (1050, 1150), (800, 900)]
    #  A: nobody's lower > 1300 -> rank 1
    #  B: A's lower(1200) > B.upper(1100) -> 1 beats it -> rank 2
    #  C: A's lower(1200) > C.upper(1150) -> 1 beats it -> rank 2 (ties B; C/B overlap)
    #  D: A,B,C all have lower > D.upper(900) -> 3 beat it -> rank 4
    assert rank_by_ci(bounds) == [1, 2, 2, 4]


def test_rank_by_ci_all_overlap_share_rank_one():
    from app.ranking import rank_by_ci

    assert rank_by_ci([(1000, 1100), (1010, 1110), (990, 1090)]) == [1, 1, 1]


def test_rank_by_ci_empty():
    from app.ranking import rank_by_ci

    assert rank_by_ci([]) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ranking.py -q`
Expected: FAIL — `cannot import name 'rank_by_ci'`.

- [ ] **Step 3: Implement `rank_by_ci` in `app/ranking.py`** (after `bradley_terry`, before the `SignificanceResult` dataclass)

```python
def rank_by_ci(bounds: list[tuple[float, float]]) -> list[int]:
    """CI-grouped 'Rank (Upper Bound)' — overlapping 95% CIs share a rank.

    bounds[i] = (lower_i, upper_i). A model j outranks i only when its lower bound
    is strictly above i's upper bound (non-overlapping intervals), so
    rank_i = 1 + #{ j : lower_j > upper_i }. Mirrors LMArena's 'Rank (UB)': models
    that are not statistically separable share a rank number.
    """
    return [1 + sum(1 for (lo_j, _hi_j) in bounds if lo_j > hi_i) for (_lo_i, hi_i) in bounds]
```

- [ ] **Step 4: Run to verify the pure fn passes**

Run: `.venv/bin/python -m pytest tests/test_ranking.py -q`
Expected: PASS.

- [ ] **Step 5: Wire into `_leaderboard_rows` (`app/main.py`)**

Replace the tail of `_leaderboard_rows` (the sort + sequential rank, currently lines 280-283):

```python
    rows.sort(key=lambda x: x["bt_score"], reverse=True)
    # CI-grouped rank (overlapping 95% CIs share a rank), computed on the displayed
    # (rounded) bounds so the rank matches the numbers shown.
    ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    for row, rank in zip(rows, ranks):
        row["rank"] = rank
    # CI whisker-bar geometry: position each [lower, point, upper] as a percent of the
    # column's full value span so ties are visible at a glance.
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

Confirm `ranking` is already imported in `app/main.py` (it is used elsewhere). After editing, `grep -n "import.*ranking\|from . import" app/main.py` to confirm.

- [ ] **Step 6: Write the route-level shared-rank test (`tests/test_leaderboard.py`, create)**

```python
"""Tests for the leaderboard credibility surface: CI-grouped rank + CI bar."""

from __future__ import annotations

from app.main import _leaderboard_rows
from app.database import SessionLocal
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)


def test_leaderboard_rows_have_ci_bar_geometry():
    with SessionLocal() as db:
        rows = _leaderboard_rows(db, "overall", None)
    assert rows, "expected seeded generators on the global overall board"
    for r in rows:
        assert 0.0 <= r["ci_left"] <= 100.0
        assert 0.0 <= r["ci_width"] <= 100.0
        assert "rank" in r


def test_ci_grouped_rank_matches_formula():
    # Directly exercise the rank rule against the serialized rows.
    from app.ranking import rank_by_ci

    with SessionLocal() as db:
        rows = _leaderboard_rows(db, "overall", None)
    expected = rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    assert [r["rank"] for r in rows] == expected
    # Rank 1 always exists; ranks are non-decreasing down the (point-sorted) board.
    assert rows[0]["rank"] == 1
    assert all(rows[i]["rank"] <= rows[i + 1]["rank"] for i in range(len(rows) - 1))
```

- [ ] **Step 7: Run the new tests + lint**

Run: `.venv/bin/python -m pytest tests/test_ranking.py tests/test_leaderboard.py -q && ruff check app/ranking.py app/main.py tests/`
Expected: PASS; ruff clean.

- [ ] **Step 8: Commit**

```bash
git add app/ranking.py app/main.py tests/test_ranking.py tests/test_leaderboard.py
git commit -m "feat(leaderboard): CI-grouped Rank (UB) + CI-bar geometry

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 2: CI whisker bar + "Rank (UB)" in the leaderboard view

**Files:**

- Modify: `app/templates/leaderboard.html`
- Modify: `app/static/style.css`
- Test: `tests/test_leaderboard.py` (extend with a render assertion)

**Interfaces:**

- Consumes: row fields `rank, ci_left, ci_width, ci_point, bt_lower, bt_upper` from Task 1.

- [ ] **Step 1: Update the table header + CI cell in `app/templates/leaderboard.html`**

Change the header row (currently line 36-37) so `#` becomes a labelled rank and the CI column hosts the bar:

```html
<tr>
  <th title="Models with overlapping 95% CIs share a rank">Rank (UB)</th>
  <th>Generator</th>
  <th>Kind</th>
  <th>BT score</th>
  <th>95% CI</th>
  <th>Elo</th>
  <th>Games</th>
</tr>
```

Replace the CI cell (currently line 46) with a whisker bar + the numbers beneath it:

```html
<td class="ci-cell">
  <div
    class="ci-bar"
    title="95% bootstrap CI [{{ r.bt_lower }}, {{ r.bt_upper }}]"
  >
    <span
      class="ci-range"
      style="left: {{ r.ci_left }}%; width: {{ r.ci_width }}%;"
    ></span>
    <span class="ci-dot" style="left: {{ r.ci_point }}%;"></span>
  </div>
  <span class="ci-nums">[{{ r.bt_lower }}, {{ r.bt_upper }}]</span>
</td>
```

Update the explanatory `.subtle` paragraph (lines 22-26) to mention the rank rule — replace its text with:

```html
{{ total_votes }} votes cast. <b>Rank (UB)</b> groups models whose 95% bootstrap
CIs overlap into the same rank (they are not statistically separable). BT =
Bradley–Terry score (Elo-scaled); the bar shows the 95% CI with the point
estimate marked. Elo updates live per vote. Recompute in Admin.
```

- [ ] **Step 2: Add CI-bar styles to `app/static/style.css`** (append near the `.ranktable` block)

```css
/* Leaderboard CI whisker bar */
.ci-cell {
  min-width: 140px;
}
.ci-bar {
  position: relative;
  height: 8px;
  background: var(--panel2);
  border: 1px solid var(--border);
  border-radius: 999px;
  margin-bottom: 0.25rem;
}
.ci-range {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  height: 6px;
  background: rgba(91, 141, 239, 0.45); /* accent2 @ 45% */
  border-radius: 999px;
}
.ci-dot {
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
}
.ci-nums {
  font-size: 0.72rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 3: Render assertion (append to `tests/test_leaderboard.py`)**

```python
def test_leaderboard_page_renders_rank_ub_and_ci_bar():
    from fastapi.testclient import TestClient
    from app.main import app

    html = TestClient(app).get("/leaderboard").text
    assert "Rank (UB)" in html
    assert "ci-bar" in html  # the whisker bar is present
```

- [ ] **Step 4: Run tests + lint**

Run: `.venv/bin/python -m pytest tests/test_leaderboard.py -q && ruff check tests/`
Expected: PASS. (CSS/HTML are formatted by the prettier hook; no `==` introduced in the template.)

- [ ] **Step 5: Commit**

```bash
git add app/templates/leaderboard.html app/static/style.css tests/test_leaderboard.py
git commit -m "feat(leaderboard): CI whisker bar + Rank (UB) header

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 3: Lock in tie handling (regression test) + methodology note

**Files:**

- Test: `tests/test_leaderboard.py` (extend) — service-level tie behavior
- Modify: `app/templates/methodology.html` (read first to match structure/wording)

**Interfaces:**

- Consumes: `service._matches_for_scope` (existing).

- [ ] **Step 1: Write the tie-handling regression test (append to `tests/test_leaderboard.py`)**

```python
def test_tie_is_split_into_both_directions_for_bt():
    """A 'tie' vote must feed Bradley-Terry as one win in EACH direction (not dropped)."""
    from app import service
    from app.models import Comparison, Criterion, ModelOutput, Vote

    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.commit()
        crit = db.query(Criterion).filter_by(slug="overall").one()
        a = db.query(ModelOutput).filter_by(is_gold=False).first()
        b = (
            db.query(ModelOutput)
            .filter(ModelOutput.task_id == a.task_id, ModelOutput.id != a.id, ~ModelOutput.is_gold)
            .first()
        )
        comp = Comparison(
            task_id=a.task_id, output_a_id=a.id, output_b_id=b.id,
            criterion_id=crit.id, session_id="tie-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="tie", session_id="tie-sess"))
        db.commit()

        matches = service._matches_for_scope(db, crit.id, None)
        ga, gb = a.generator_id, b.generator_id
    # The single tie contributes BOTH orderings — split credit, not dropped.
    assert (ga, gb) in matches
    assert (gb, ga) in matches


def test_bad_vote_excluded_from_matches():
    from app import service
    from app.models import Comparison, Criterion, ModelOutput, Vote

    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.commit()
        crit = db.query(Criterion).filter_by(slug="overall").one()
        a = db.query(ModelOutput).filter_by(is_gold=False).first()
        b = (
            db.query(ModelOutput)
            .filter(ModelOutput.task_id == a.task_id, ModelOutput.id != a.id, ~ModelOutput.is_gold)
            .first()
        )
        comp = Comparison(
            task_id=a.task_id, output_a_id=a.id, output_b_id=b.id,
            criterion_id=crit.id, session_id="bad-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="bad", session_id="bad-sess"))
        db.commit()
        matches = service._matches_for_scope(db, crit.id, None)
    assert matches == []  # 'bad' contributes nothing
```

- [ ] **Step 2: Run to confirm GREEN (documents existing behavior)**

Run: `.venv/bin/python -m pytest tests/test_leaderboard.py -q`
Expected: PASS (these lock in already-correct behavior). If either FAILS, STOP — that means tie/bad handling is NOT as believed; report it rather than altering the test to pass.

- [ ] **Step 3: Add a methodology note (`app/templates/methodology.html`)**

Read `app/templates/methodology.html` first. Add a short prose item (matching the page's existing `.prose` structure) covering:

- **Rank (UB):** models whose 95% bootstrap CIs overlap share a rank; a model outranks another only when its lower bound exceeds the other's upper bound.
- **Ties:** a tie is credited as a split — one win in each direction — so it informs Bradley–Terry symmetrically without a separate tie parameter; "both bad" votes are recorded but excluded from the fit.

Keep it to ~4-6 sentences, no `==` in any Jinja.

- [ ] **Step 4: Confirm the methodology page still renders (append to `tests/test_leaderboard.py`)**

```python
def test_methodology_mentions_rank_ub_and_ties():
    from fastapi.testclient import TestClient
    from app.main import app

    html = TestClient(app).get("/methodology").text
    assert "Rank (UB)" in html
    assert "tie" in html.lower()
```

- [ ] **Step 5: Run tests + lint; commit**

Run: `.venv/bin/python -m pytest tests/test_leaderboard.py -q && ruff check tests/`

```bash
git add tests/test_leaderboard.py app/templates/methodology.html
git commit -m "test(leaderboard): lock in tie split + bad exclusion; methodology note

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

### Task 4: Full suite + live-verify + docs (no merge)

**Files:**

- Modify: `README.md` (leaderboard methodology blurb + test count)
- Modify: `docs/audits/2026-06-20-field-audit.md` (flip B1 checkboxes)
- Test: full suite

- [ ] **Step 1: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q && ruff check app/ tests/ scripts/`
Expected: PASS (prior 59 + the new ranking/leaderboard tests). If any pre-existing test asserts an exact leaderboard `rank` sequence (e.g. expecting `1,2,3,4,5`), it may now see shared ranks — update it to assert the CI-grouped rule (or `rank[0]==1` + non-decreasing), NOT to revert the feature. If a failure is a real regression, STOP and report.

- [ ] **Step 2: Live-verify under uvicorn**

```bash
export BIO3D_DATA_DIR="$(mktemp -d)" BIO3D_ADMIN_TOKEN="live" BIO3D_GOLD_RATE="0"
.venv/bin/python -c "from app.seed import seed_all; seed_all(force=True)"
.venv/bin/python -m uvicorn app.main:app --port 8099 >/tmp/uv_lb.log 2>&1 &
sleep 3
# Cast a few votes so CIs are non-degenerate, recompute, then check the board.
curl -s "http://127.0.0.1:8099/api/leaderboard" | .venv/bin/python -c "import sys,json; r=json.load(sys.stdin)['rows']; print('rows', len(r)); print('ranks', [x['rank'] for x in r]); print('has ci bar geom', all('ci_left' in x for x in r))"
curl -s -o /dev/null -w 'leaderboard http=%{http_code}\n' "http://127.0.0.1:8099/leaderboard"
pkill -f 'uvicorn app.main:app'
```

Expected: rows listed, ranks present (possibly with shared values), `ci_left` present on each row, HTML 200. Note in the report that the visual bar rendering itself isn't pixel-verified (no headless browser — playwright is the Increment-3 prerequisite).

- [ ] **Step 3: Update `README.md`** — in the leaderboard/methodology section, add one line on CI-grouped Rank (UB) + the CI bar; bump the `pytest -q` test count to the new total.

- [ ] **Step 4: Flip audit checkboxes** in `docs/audits/2026-06-20-field-audit.md` (read first, match wording): B1 "CI-grouped Rank (Upper Bound)" → `[x]`; B1 "Per-row CI + vote-count columns" → `[x]` (now a bar + numbers + Games); B1 "Explicit tie handling … verify ties aren't dropped" → `[x]` (verified correct + regression-tested). Leave per-category/multi-dimension/style-control/active-sampling `[ ]`.

- [ ] **Step 5: Commit (do NOT merge)**

```bash
git add README.md docs/audits/2026-06-20-field-audit.md
git commit -m "docs(leaderboard): Rank (UB) + CI bar in README/audit; tie handling verified

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01N8JSB5JnrqT2N1hNUeYYoW"
```

---

## Self-Review

- **Spec coverage:** CI-grouped rank (Task 1), CI bar + Rank (UB) header (Task 2), tie handling verified + methodology (Task 3), suite/verify/docs (Task 4). All three audit B1 "surfaced credibility" items covered; deferred B1 items (per-category, multi-dimension, style control, active sampling) explicitly out of scope.
- **Type consistency:** `rank_by_ci(list[tuple[float,float]]) -> list[int]` used identically in Task 1 impl, the pure-fn test, and the route test. Row keys `ci_left/ci_width/ci_point/rank` produced in Task 1, consumed in Task 2's template and tests.
- **No placeholders:** every step has runnable code/commands.
- **Known fragility:** a pre-existing test may assert a strict `1..N` rank sequence (Task 4 Step 1 handles this — assert the rule, never revert the feature). The CI-bar geometry uses rounded bounds; degenerate all-equal CIs give `span=1.0` fallback (no divide-by-zero).
