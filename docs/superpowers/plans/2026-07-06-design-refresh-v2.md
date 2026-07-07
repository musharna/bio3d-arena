# Bio 3D Arena v2 Design Refresh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Claude "Bio 3D Arena v2" design handoff (left-sidebar shell, OKLCH green token system, sticky kingdom scope bar, light/dark theming, ~13 hi-fi screens) onto the existing server-rendered FastAPI + Jinja2 app, and wire kingdom (Plants/Fungi/Animals) as a global data filter across every page.

**Architecture:** Keep the app **server-rendered Jinja2** (NOT an SPA — the handoff prototype is an SPA reference only; the README says "recreate in the codebase's own patterns"). Every template continues to `{% extends "base.html" %}`. The re-skin is: (1) a rewritten `style.css` token system + component styles, (2) a new sidebar `base.html` shell + `shell.js`, (3) a static kingdom↔category map + request-scoped `kingdom` filter threaded into existing data builders, (4) per-page template restyles that preserve every JS DOM contract.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Jinja2, vanilla JS, `<model-viewer>` 3.5.0 + 3Dmol.js (CDN, unchanged), Google Fonts (Space Grotesk / IBM Plex Sans / IBM Plex Mono), CSS custom properties in OKLCH.

**Design source of truth (the pixel/copy reference for every task):**

- Screenshots: `<HANDOFF>/screenshots/01..13-*.png` (dark theme, intended layout/hierarchy).
- Prototype markup + exact copy + SVGs: `<HANDOFF>/Bio 3D Arena v2.dc.html` (2365 lines). Landmarks: brand mark SVG `:90`; nav-icon `icon(name)` helper `:966`; accent/theme props `:960`; live-pulse dot `:207`; `syncPair` `:1296`.
- Token tables + screen specs: `<HANDOFF>/README.md` and `<HANDOFF>/DESIGN_SYSTEM.md`.
- `<HANDOFF>` = `/home/mjarnold/.claude/jobs/c4d42ae1/tmp/design/design_handoff_bio3d_arena` (also mirrored — copy the folder into `docs/design-handoff/` in Task 0 so it is repo-local for implementers).

Copy is FINAL: lift exact strings from the prototype HTML / screenshots. Do not invent copy.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Server-rendered only.** No React/Vue/SPA. Templates extend `base.html`; data comes from the FastAPI route context, not client fetch (except the arena, which already fetches `/api/next`).
- **OKLCH tokens, both themes.** Use the exact OKLCH values from `<HANDOFF>/README.md` "Design Tokens" — dark palette is default, light palette under `:root[data-theme="light"]`. Default accent = `oklch(0.56 0.13 150)` (chlorophyll green).
- **Theme:** `data-theme` attribute on `<html>`; `dark` default; persist to `localStorage['bio3d_theme']`; honor `prefers-color-scheme` when no stored value; **no-FOUC inline `<head>` script** sets the attribute before first paint.
- **Kingdom scope:** value ∈ `{all, plants, fungi, animals}` (microbes stays out of the switcher — no live data). Read from `?kingdom=` query param, persisted in a cookie; exposed as `request.state.kingdom`; `all` == no filter. Buckets: **plants = {`plants`, `synthetic-plants`}**, fungi = {`fungi`}, animals = {`animals`}. Selecting **Animals** (a coming-soon kingdom with no live tasks) routes to the roadmap screen instead of the normal page.
- **PRESERVE ALL JS DOM CONTRACTS — renaming any of these breaks the app:**
  - Arena (`arena.js`): ids `#sel-category`, `#sel-criterion`, `#task-cat`, `#task-title`, `#task-prompt`, `#criterion-name`, `#task-card`, `#reference-panel`, `#reference-gallery`, `#fmt-a`, `#fmt-b`, `#slot-a`, `#slot-b`, `#ai-badge-a`, `#ai-badge-b`, `#kwise-grid`, `#kwise-allbad`, `#status-line`, `#onboard-banner`, `#onboard-dismiss`, `#reference-lightbox`, `#reference-lightbox-img`, `#reference-lightbox-credit`; classes `.ab-btn`, `.pair .model-col`, `.vote-bar .vote-btn` (this scoped selector must stay — `arena.js:413` deliberately excludes `.kwise-pick-btn`); data-attrs `data-winner`, `data-ab`. `arena.js:192-243` builds K-wise cards as `div.model-col.kwise-cell > (div.model-label + span.fmt-chip [+ span.ai-badge]) + div.viewer-slot + button.vote-btn.win.kwise-pick-btn` — style via these classes, do NOT change the shape.
  - Viewer (`viewer.js`): container class `.viewer-slot`; `window.Bio3DViewer.mount(slot, asset, onFlag)` / `.syncPair(a,b)`; generated classes `.viewer-loading`, `.viewer-spinner`, `.viewer-hint`, `.viewer-error`, `.viewer-controls`, `.viewer-ctl` (restyle freely, don't rename).
  - Spotlight (`spotlight.js`): `#live-viewer-slot`, `.spotlight-card`(`.active`/`.hidden`), `.thumb`(`data-asset`), `#spotlight-toolbar`(`data-cap`), `.chip`(`data-filter-cls`), `#scored-only`, `#spotlight-search`, `#result-count`, `.group`, `.show-all-btn`, card data-attrs `data-cls`/`data-scored`/`data-label`/`data-chamfer`.
  - Moderation/admin (`moderation.js`, inline `admin.html`): `.mod-viewer`(`data-url`/`data-format`), `#admin-token`, `.token-mirror`.
  - Benchmark inline script (`benchmark.html:66-92`) and submit inline script (`submit.html:29-48`) — preserve their element ids.
- **Accessibility:** `:focus-visible` ring `2px solid var(--accent)` offset 2px on all interactive elements; `aria-label` on icon-only controls; viewer toolbars stay visible under `@media (hover:none)`.
- **Motion:** all animations respect `@media (prefers-reduced-motion: reduce)` (collapse to ~0).
- **Layout:** content max-width 1180px (820px on roadmap/stub), 30px horizontal gutter.
- **Tests:** `pytest -q` from repo root. NEVER set `BIO3D_DATABASE_URL` to a study/production DB (`conftest.py` guards this). New backend logic gets real unit tests; template/CSS tasks verify by booting the app and asserting HTTP 200 + key markup via curl (see Task 0 for the smoke harness).
- **No new heavy deps.** Fonts via Google Fonts `<link>`; icons are inline SVG (from the prototype); no icon fonts, no CSS frameworks.
- **Commits:** conventional-commit messages; trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01RBu1Gn6emZ3cNwmeYBXvrU`.

---

## File Structure

**New files**

- `docs/design-handoff/` — repo-local copy of the handoff bundle (Task 0).
- `app/kingdoms.py` — static kingdom↔category map + resolvers.
- `app/static/shell.js` — sidebar collapse, theme toggle, mobile drawer, kingdom selector (all persisted).
- `app/templates/home.html` — new landing page (moves arena off `/`).
- `app/templates/models.html`, `app/templates/model_detail.html` — new Models index + detail.
- `app/templates/_scope_pill.html`, `app/templates/_kingdom_roadmap.html` — small reusable partials.
- `tests/test_kingdoms.py`, `tests/test_kingdom_scope.py`, `tests/test_kingdom_filtering.py`, `tests/test_models_page.py`, `tests/test_home_route.py`, `tests/test_smoke_pages.py`.

**Heavily modified**

- `app/static/style.css` — token system rewrite + component restyle (all families).
- `app/templates/base.html` — sidebar shell, kingdom bar, footer rail, `<head>` fonts + no-FOUC script.
- `app/main.py` — `request.state.kingdom` middleware, kingdom resolver, kingdom threaded into every data builder, new `/`, `/arena`, `/models`, `/models/{slug}` routes.
- `app/service.py`, `app/matchmaking.py`, `app/difficulty.py`, `app/recon_service.py`, `app/spotlight.py` — accept a category-id **set** where they take a single `category_id` today.
- Every page template — restyle to the token system.

**Responsibility boundaries:** tokens/shell/kingdom-spine are foundational (Tasks 1–8) and every page task depends on them. Page tasks (9–24) are mutually independent restyles and can be reviewed in isolation.

---

## Task 0: Repo-local handoff + smoke-test harness

**Files:**

- Create: `docs/design-handoff/` (copy of the bundle)
- Create: `tests/test_smoke_pages.py`

**Interfaces:**

- Produces: `smoke_get(client, path)` pattern reused by later page tasks; a repo-local `docs/design-handoff/` path.

- [ ] **Step 1: Copy the handoff bundle into the repo** (so implementers/reviewers have the pixel source without the external tmp path)

```bash
cp -r /home/mjarnold/.claude/jobs/c4d42ae1/tmp/design/design_handoff_bio3d_arena docs/design-handoff
git add docs/design-handoff && git status --short | head
```

- [ ] **Step 2: Write a smoke test that every public page returns 200** (baseline BEFORE any change — it must pass on the current site first)

```python
# tests/test_smoke_pages.py
import pytest
from starlette.testclient import TestClient
from app.main import app

PUBLIC_PATHS = [
    "/", "/leaderboard", "/significance", "/benchmark", "/dataset",
    "/difficulty", "/tasks", "/submit", "/coverage", "/procedural",
    "/fidelity", "/methodology", "/terms", "/privacy", "/licenses",
    "/spotlight", "/methodology",
]

@pytest.fixture
def client():
    return TestClient(app)

@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_page_200(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    assert b"<html" in r.content.lower()
```

- [ ] **Step 3: Run it against the current (unchanged) site**

Run: `pytest -q tests/test_smoke_pages.py`
Expected: PASS for all current paths (this is the regression baseline; `/arena`, `/models` are added later and appended to `PUBLIC_PATHS` in their tasks).

- [ ] **Step 4: Commit**

```bash
git add docs/design-handoff tests/test_smoke_pages.py
git commit -m "chore(design): repo-local handoff bundle + page smoke-test baseline"
```

---

## Task 1: Design token system + fonts + app background

**Files:**

- Modify: `app/static/style.css:154-208` (the `:root` block + base rules) and add new token/theme/background/font rules at the top of the cascade.
- Test: `tests/test_smoke_pages.py` (re-run; visual by boot).

**Interfaces:**

- Produces: the full CSS custom-property vocabulary every later task styles against. Token names (keep the 8 existing names so current component rules keep working, ADD the rest): `--bg --navBg --panel --panelDeep --panel2 --border --text --muted --faint --rowAlt --accent --accent2 --win --tie --bad --amber --stage1 --stage2 --stageFrame --vignette --ctlBg --shadowCard --shadowLift`, plus `--wash --wash2 --gridDot`, plus radius/space scale `--r-card:14px --r-ctl:9px --r-pill:999px`.

- [ ] **Step 1: Add the token blocks at the very top of `style.css`** (above the existing "appended at top" block so vars resolve for everything). Use the exact OKLCH values from `<HANDOFF>/README.md` "Design Tokens". Structure:

```css
/* === Bio 3D Arena v2 design tokens (OKLCH). Dark = default. === */
:root {
  /* dark palette — values verbatim from README "Dark palette (default)" */
  --bg: oklch(0.165 0.018 258);
  --navBg: oklch(0.2 0.02 258 / 0.85);
  --panel: oklch(0.215 0.021 258);
  --panelDeep: oklch(0.195 0.02 258);
  --panel2: oklch(0.255 0.023 258);
  --border: oklch(0.325 0.025 258);
  --text: oklch(0.95 0.006 258);
  --muted: oklch(0.72 0.02 258);
  --faint: oklch(0.58 0.02 258);
  --rowAlt: oklch(0.19 0.02 258);
  --shadowCard: 0 1px 2px oklch(0 0 0 / 0.24), 0 10px 30px oklch(0 0 0 / 0.26);
  --shadowLift: 0 2px 6px oklch(0 0 0 / 0.3), 0 20px 50px oklch(0 0 0 / 0.4);
  --stage1: oklch(0.3 0.032 258);
  --stage2: oklch(0.135 0.018 258);
  --stageFrame: oklch(0.145 0.018 258);
  --vignette: oklch(0.1 0.015 258 / 0.55);
  --ctlBg: oklch(0.16 0.02 258 / 0.78);
  /* shared accent/semantic (dark) */
  --accent: oklch(0.72 0.14 150);
  --accent2: oklch(0.76 0.1 205);
  --win: oklch(0.78 0.15 142);
  --tie: oklch(0.68 0.04 258);
  --bad: oklch(0.64 0.17 26);
  --amber: oklch(0.78 0.14 78);
  /* washes for the app background */
  --wash: oklch(0.72 0.14 150 / 0.06);
  --wash2: oklch(0.76 0.1 205 / 0.05);
  --gridDot: oklch(0.5 0.03 205 / 0.05);
  /* shape scale */
  --r-card: 14px;
  --r-ctl: 9px;
  --r-pill: 999px;
}
:root[data-theme="light"] {
  /* light palette — values verbatim from README "Light palette" */
  --bg: oklch(0.963 0.006 250);
  --navBg: oklch(0.99 0.004 258 / 0.85);
  --panel: oklch(1 0 0);
  --panelDeep: oklch(0.987 0.004 258);
  --panel2: oklch(0.955 0.008 250);
  --border: oklch(0.9 0.008 258);
  --text: oklch(0.26 0.02 258);
  --muted: oklch(0.48 0.02 258);
  --faint: oklch(0.62 0.02 258);
  --rowAlt: oklch(0.965 0.006 258);
  --shadowCard:
    0 1px 2px oklch(0.5 0.03 258 / 0.05), 0 8px 24px oklch(0.5 0.04 258 / 0.08);
  --shadowLift:
    0 4px 10px oklch(0.5 0.03 258 / 0.08),
    0 20px 48px oklch(0.5 0.05 258 / 0.14);
  --stage1: oklch(0.93 0.008 258);
  --stage2: oklch(0.8 0.012 258);
  --stageFrame: oklch(0.9 0.01 258);
  --vignette: oklch(0.55 0.02 258 / 0.15);
  --ctlBg: oklch(1 0 0 / 0.82);
  --accent: oklch(0.5 0.13 150);
  --accent2: oklch(0.52 0.11 208);
  --win: oklch(0.62 0.15 142);
  --tie: oklch(0.6 0.04 258);
  --amber: oklch(0.78 0.14 78);
  --wash: oklch(0.5 0.13 150 / 0.05);
  --wash2: oklch(0.52 0.11 208 / 0.05);
  --gridDot: oklch(0.5 0.03 205 / 0.06);
}
```

- [ ] **Step 2: Delete the now-duplicated old `:root` block** at `style.css:154-163` (the 8 hardcoded hex vars) so there is one source of truth. Verify every old var name (`--bg --panel --panel2 --text --muted --accent --accent2 --border`) is present in the new blocks (it is).

- [ ] **Step 3: Add fonts + app background + base typography.** In `base.html:<head>` add the Google Fonts links (Task 2 does the `<head>`; here just the CSS `font-family` rules). Add near the top of `style.css`:

```css
body {
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  color: var(--text);
  background: var(--bg);
  background-image:
    radial-gradient(130% 80% at 12% -8%, var(--wash), transparent 52%),
    radial-gradient(120% 70% at 92% 4%, var(--wash2), transparent 46%),
    radial-gradient(var(--gridDot) 1px, transparent 1.4px);
  background-size:
    auto,
    auto,
    26px 26px;
  background-attachment: fixed;
}
h1,
h2,
h3,
.b3d-display {
  font-family: "Space Grotesk", sans-serif;
  letter-spacing: -0.02em;
}
.b3d-mono,
.eyebrow,
kbd,
.mono {
  font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums;
}
```

(Keep the existing `body` rule's `line-height:1.5`; merge, don't duplicate.)

- [ ] **Step 4: Boot the app and confirm it renders with the new palette** (no test framework can assert visual color; assert boot + token presence)

Run:

```bash
BIO3D_DATA_DIR=/tmp/bio3d_test_tok python -c "from app.main import app; from starlette.testclient import TestClient; c=TestClient(app); r=c.get('/leaderboard'); assert r.status_code==200; print('ok', len(r.content))"
grep -c "oklch(0.165 0.018 258)" app/static/style.css
```

Expected: `ok <bytes>` and grep ≥1.

- [ ] **Step 5: Run the smoke suite** — Run: `pytest -q tests/test_smoke_pages.py` — Expected: PASS (palette swap must not break any page).

- [ ] **Step 6: Commit** — `git commit -am "feat(design): OKLCH token system (dark+light) + fonts + app background"`

---

## Task 2: App shell — sidebar, kingdom scope bar, footer, theme/collapse/mobile JS

**Files:**

- Modify: `app/templates/base.html` (full shell rewrite; preserve the `request.state` auth block + the 2 blocks + CDN `<head>` scripts).
- Create: `app/static/shell.js`
- Modify: `app/static/style.css` (add shell component families: `.b3d-app`, `.b3d-sidebar`, `.b3d-nav*`, `.b3d-kbar`, `.b3d-footer`; update/replace `.topbar` family `style.css:210-249`).

**Interfaces:**

- Consumes: tokens from Task 1.
- Produces: the shell every page renders inside; `data-theme` on `<html>`; a `{% block content %}` that now sits inside `.b3d-main`; `request.state.kingdom` is READ here (set in Task 4) to mark the active kingdom tab and render the scope pill — until Task 4 lands, default to `all`.

- [ ] **Step 1: Rewrite `base.html`.** Structure (server-rendered; extract exact SVGs/copy from the prototype):
  - `<html data-theme="dark">` + no-FOUC `<head>` script: `<script>try{var t=localStorage.getItem('bio3d_theme')||(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>` placed BEFORE the stylesheet link.
  - Add Google Fonts: `<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">`.
  - Keep the 3 existing CDN/script tags (`model-viewer`, `3Dmol`, `/static/viewer.js`) and add `<script defer src="/static/shell.js"></script>`.
  - Body layout: `.b3d-app` (flex) → `.b3d-sidebar` + `.b3d-content`.
  - **Sidebar** (`aria` labelled): logo tile with the tree-in-hexagon SVG (copy verbatim from `<HANDOFF>/Bio 3D Arena v2.dc.html:90`) + wordmark "Bio 3D" (Space Grotesk, "3D" in `--accent`) + collapse toggle button (`#b3d-collapse`, `aria-label="Collapse sidebar"`). Grouped nav with mono uppercase headings — extract the group/label/icon set from the prototype `icon(name)` helper `:966` and the sidebar-groups builder `:1008`:
    - Overview: Home (`/`), Arena (`/arena`)
    - Rankings: Leaderboard (`/leaderboard`), Models (`/models`), Difficulty (`/difficulty`)
    - Analysis: Benchmark (`/benchmark`), Coverage (`/coverage`), Significance (`/significance`)
    - Data: Dataset (`/dataset`), Tasks (`/tasks`), Spotlight (`/spotlight`)
    - About: Methodology (`/methodology`), Submit (`/submit`)
      Each item: 20px inline-SVG icon slot + label; active item (match `request.url.path`) gets `.is-active` (accent text + `color-mix(in oklch, var(--accent) 14%, transparent)` bg). Bottom: theme toggle button (`#b3d-theme`, sun/moon glyph + "Theme") and the auth block moved from the old nav (`{% if request.state.user %}…{% elif request.state.login_enabled %}…`).
  - **Content column** = kingdom scope bar (sticky) + `<main class="b3d-main">{% block content %}{% endblock %}</main>` + footer.
  - **Kingdom scope bar** (`.b3d-kbar`, `position:sticky; top:0`): mono "KINGDOM" label + segmented control (`All · 🌿 Plants · 🍄 Fungi · 🐾 Animals`), each a link `?kingdom=<k>` with `.is-active` when `request.state.kingdom == k` (default `all`). Right side (wide only, hide ≤1120px): compact top-nav duplicate (Arena/Leaderboard/Models).
  - **Footer resource rail** (`.b3d-footer`, border-top): mono chips `Paper ↗ · GitHub ↗ · Dataset ↗ · Submit a model` + the existing `Terms · Privacy · Licenses` links + the existing tagline.
  - **Mobile:** hamburger button (`#b3d-burger`, ≤760px, top-left) toggles `.b3d-sidebar.is-open` over a scrim (`.b3d-scrim`).

- [ ] **Step 2: Write `shell.js`** (vanilla, no deps):

```js
// app/static/shell.js — sidebar collapse, theme toggle, mobile drawer. All persisted.
(function () {
  var root = document.documentElement;
  function setTheme(t) {
    root.setAttribute("data-theme", t);
    try {
      localStorage.setItem("bio3d_theme", t);
    } catch (e) {}
  }
  var themeBtn = document.getElementById("b3d-theme");
  if (themeBtn)
    themeBtn.addEventListener("click", function () {
      setTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light");
    });
  var COLLAPSE_KEY = "bio3d_nav_collapsed";
  var app = document.querySelector(".b3d-app");
  try {
    if (localStorage.getItem(COLLAPSE_KEY) === "1" && app)
      app.classList.add("is-collapsed");
  } catch (e) {}
  var collapseBtn = document.getElementById("b3d-collapse");
  if (collapseBtn && app)
    collapseBtn.addEventListener("click", function () {
      var c = app.classList.toggle("is-collapsed");
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "1" : "0");
      } catch (e) {}
    });
  var burger = document.getElementById("b3d-burger");
  var sidebar = document.querySelector(".b3d-sidebar");
  var scrim = document.querySelector(".b3d-scrim");
  function closeDrawer() {
    if (sidebar) sidebar.classList.remove("is-open");
    if (scrim) scrim.classList.remove("is-open");
  }
  if (burger && sidebar)
    burger.addEventListener("click", function () {
      sidebar.classList.toggle("is-open");
      if (scrim) scrim.classList.toggle("is-open");
    });
  if (scrim) scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeDrawer();
  });
})();
```

- [ ] **Step 3: Add shell CSS** — `.b3d-app{display:flex;min-height:100vh}`, `.b3d-sidebar{position:fixed;width:248px;…}`, `.b3d-app.is-collapsed .b3d-sidebar{width:72px}` (hide labels), `.b3d-content{margin-left:248px}` / collapsed `72px`, `.b3d-kbar{position:sticky;top:0;background:var(--navBg);backdrop-filter:blur(10px);…}`, `.b3d-main{max-width:1180px;margin:0 auto;padding:30px}`, mobile `@media (max-width:760px)` off-canvas (`transform:translateX(-100%)`, `.is-open{transform:none}`), plus focus rings + `prefers-reduced-motion`. Replace the old `.topbar`/`.brand`/nav rules (`style.css:210-238`) — but KEEP `.nav-burger`/`#nav-toggle` selectors only if still referenced; otherwise remove and update the mobile media block (`style.css:718-729`) which references `#nav-toggle:checked ~ nav`.

- [ ] **Step 4: Boot + assert shell markup present**

Run:

```bash
python -c "from app.main import app; from starlette.testclient import TestClient; c=TestClient(app); h=c.get('/leaderboard').text; assert 'b3d-sidebar' in h and 'b3d-kbar' in h and 'shell.js' in h and 'Space+Grotesk' in h; print('shell ok')"
```

Expected: `shell ok`.

- [ ] **Step 5: Run smoke suite** — `pytest -q tests/test_smoke_pages.py` — Expected: PASS.

- [ ] **Step 6: Commit** — `git commit -am "feat(design): sidebar app shell + kingdom bar + footer rail + shell.js (theme/collapse/mobile)"`

---

## Task 3: Kingdom↔category map (`app/kingdoms.py`)

**Files:**

- Create: `app/kingdoms.py`
- Create: `tests/test_kingdoms.py`

**Interfaces:**

- Produces:
  - `KINGDOMS = ("plants", "fungi", "animals")` (switcher order; `all` handled separately)
  - `KINGDOM_OF: dict[str,str]` — category slug → kingdom
  - `CATEGORY_SLUGS_IN: dict[str, frozenset[str]]` — kingdom → category slugs
  - `KINGDOM_EMOJI: dict[str,str]` — `{"all":"🧬","plants":"🌿","fungi":"🍄","animals":"🐾"}`
  - `KINGDOM_LABEL: dict[str,str]` — `{"all":"All kingdoms","plants":"Plants","fungi":"Fungi","animals":"Animals"}`
  - `normalize_kingdom(value: str | None) -> str` — returns a valid kingdom or `"all"`.
  - `category_ids_for_kingdom(db, kingdom: str) -> set[int] | None` — `None` when `kingdom == "all"` (means "no filter"); else the set of `Category.id` whose slug ∈ the kingdom's bucket (may be empty set if none exist yet).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kingdoms.py
from sqlalchemy import select
from app import kingdoms as K
from app.models import Category

def test_static_map_covers_buckets():
    assert K.KINGDOM_OF["plants"] == "plants"
    assert K.KINGDOM_OF["synthetic-plants"] == "plants"   # procedural plants stay in plants
    assert K.KINGDOM_OF["fungi"] == "fungi"
    assert K.KINGDOM_OF["animals"] == "animals"
    assert "plants" in K.CATEGORY_SLUGS_IN["plants"] and "synthetic-plants" in K.CATEGORY_SLUGS_IN["plants"]

def test_normalize():
    assert K.normalize_kingdom(None) == "all"
    assert K.normalize_kingdom("bogus") == "all"
    assert K.normalize_kingdom("PLANTS") == "plants"
    assert K.normalize_kingdom("fungi") == "fungi"

def test_category_ids_for_kingdom(db_session):
    db = db_session
    for slug, name in [("plants","Plants"),("synthetic-plants","Synthetic Plants"),("fungi","Fungi")]:
        db.add(Category(slug=slug, name=name))
    db.flush()
    assert K.category_ids_for_kingdom(db, "all") is None
    plant_ids = K.category_ids_for_kingdom(db, "plants")
    got = {db.execute(select(Category.slug).where(Category.id == i)).scalar() for i in plant_ids}
    assert got == {"plants", "synthetic-plants"}
    assert K.category_ids_for_kingdom(db, "animals") == set()  # none seeded here
```

- [ ] **Step 2: Run it, verify it fails** — `pytest -q tests/test_kingdoms.py` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement `app/kingdoms.py`**

```python
"""Kingdom ⇄ category mapping. Kingdom is a display/scope grouping OVER categories;
there is no `kingdom` column (see docs plan). Buckets are closed and small, so a static
map (mirroring app/paradigms.py) is the source of truth — unit-testable, no migration."""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Category

KINGDOMS = ("plants", "fungi", "animals")

CATEGORY_SLUGS_IN: dict[str, frozenset[str]] = {
    "plants": frozenset({"plants", "synthetic-plants"}),
    "fungi": frozenset({"fungi"}),
    "animals": frozenset({"animals"}),
}
KINGDOM_OF: dict[str, str] = {
    slug: kingdom for kingdom, slugs in CATEGORY_SLUGS_IN.items() for slug in slugs
}
KINGDOM_EMOJI = {"all": "🧬", "plants": "🌿", "fungi": "🍄", "animals": "🐾"}
KINGDOM_LABEL = {"all": "All kingdoms", "plants": "Plants", "fungi": "Fungi", "animals": "Animals"}

def normalize_kingdom(value: str | None) -> str:
    if not value:
        return "all"
    v = value.strip().lower()
    return v if v in KINGDOMS else "all"

def category_ids_for_kingdom(db: Session, kingdom: str) -> set[int] | None:
    kingdom = normalize_kingdom(kingdom)
    if kingdom == "all":
        return None
    slugs = CATEGORY_SLUGS_IN[kingdom]
    return set(db.execute(select(Category.id).where(Category.slug.in_(slugs))).scalars())
```

- [ ] **Step 4: Run the test, verify pass** — `pytest -q tests/test_kingdoms.py` — Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(kingdom): static kingdom↔category map + resolver (app/kingdoms.py)"`

---

## Task 4: Kingdom request scope (middleware + base template wiring)

**Files:**

- Modify: `app/main.py:80-113` (`ensure_session` middleware) — set `request.state.kingdom`; persist cookie.
- Modify: `app/templates/base.html` — mark the active kingdom tab from `request.state.kingdom`; render a scope pill partial.
- Create: `app/templates/_scope_pill.html`
- Create: `tests/test_kingdom_scope.py`

**Interfaces:**

- Consumes: `app.kingdoms.normalize_kingdom`.
- Produces: `request.state.kingdom` (always a valid kingdom string, default `"all"`) available to every template + route; a cookie `bio3d_kingdom` persisting the last selection; a `{% include "_scope_pill.html" %}`-able pill showing the active scope (`ALL KINGDOMS` / `PLANTS` / `FUNGI` / `ANIMALS`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kingdom_scope.py
from starlette.testclient import TestClient
from app.main import app

def test_kingdom_defaults_to_all():
    c = TestClient(app)
    r = c.get("/leaderboard")
    assert r.status_code == 200
    # scope pill shows ALL KINGDOMS by default
    assert b"ALL KINGDOMS" in r.content

def test_kingdom_query_param_sets_scope_and_cookie():
    c = TestClient(app)
    r = c.get("/leaderboard?kingdom=plants")
    assert r.status_code == 200
    assert b"PLANTS" in r.content
    assert c.cookies.get("bio3d_kingdom") == "plants"

def test_kingdom_persists_from_cookie():
    c = TestClient(app)
    c.get("/leaderboard?kingdom=fungi")            # sets cookie
    r = c.get("/tasks")                            # no param -> cookie wins
    assert b"FUNGI" in r.content

def test_bogus_kingdom_falls_back_to_all():
    c = TestClient(app)
    r = c.get("/leaderboard?kingdom=dragons")
    assert b"ALL KINGDOMS" in r.content
```

- [ ] **Step 2: Run, verify fail** — `pytest -q tests/test_kingdom_scope.py` — Expected: FAIL.

- [ ] **Step 3: Set `request.state.kingdom` in the middleware.** In `ensure_session` (`app/main.py:80-113`), after session setup, before `response = await call_next(request)`:

```python
from app import kingdoms as _kingdoms  # top-of-file import
# ... inside ensure_session, before call_next:
_kq = request.query_params.get("kingdom")
_kingdom = _kingdoms.normalize_kingdom(_kq if _kq is not None else request.cookies.get("bio3d_kingdom"))
request.state.kingdom = _kingdom
```

After `response = await call_next(request)`, persist when it came from the query param:

```python
if _kq is not None:
    response.set_cookie("bio3d_kingdom", _kingdom, max_age=60*60*24*365, samesite="lax")
```

- [ ] **Step 4: Write `_scope_pill.html`**

```html
{# renders the active-kingdom scope pill next to a page H1 #}
<span class="b3d-scope-pill"
  >{{ {"all":"ALL
  KINGDOMS","plants":"PLANTS","fungi":"FUNGI","animals":"ANIMALS"}[request.state.kingdom]
  }}</span
>
```

Add `.b3d-scope-pill` style (mono, uppercase, accent-tinted border pill) to `style.css`.

- [ ] **Step 5: Wire the kingdom bar active state in `base.html`** — each segmented link gets `.is-active` when `request.state.kingdom == "<k>"` (and `all` when it equals `all`).

- [ ] **Step 6: Run tests** — `pytest -q tests/test_kingdom_scope.py tests/test_smoke_pages.py` — Expected: PASS.

- [ ] **Step 7: Commit** — `git commit -am "feat(kingdom): request-scoped kingdom via middleware + cookie + scope pill"`

---

## Task 5: Kingdom filter — arena pool + matchmaking + `/api/meta`

**Files:**

- Modify: `app/matchmaking.py:48-78` (`pick_task` accepts a category-id set).
- Modify: `app/main.py` — `_build_comparison` (`:289-360`), `_build_kwise_comparison` (`:363-422`), `/api/meta` (`:501-511`), `/api/next` (`:514-530`) thread `request.state.kingdom`.
- Modify: `tests/` — add `tests/test_kingdom_filtering.py` (arena section).

**Interfaces:**

- Consumes: `kingdoms.category_ids_for_kingdom`.
- Produces: `pick_task(db, *, category_id=None, category_ids=None, ...)` — when `category_ids` (a set) is passed, filters `Task.category_id.in_(category_ids)`; `category_id` (single) still supported for existing callers. Arena `/api/next` restricts the pool to the active kingdom.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kingdom_filtering.py
from app import matchmaking
from app.models import Category, Task, Criterion

def _mk(db):
    p = Category(slug="plants", name="Plants"); f = Category(slug="fungi", name="Fungi")
    db.add_all([p, f]); db.flush()
    tp = Task(slug="rose", category_id=p.id, title="Rose", prompt="", active=True)
    tf = Task(slug="bolete", category_id=f.id, title="Bolete", prompt="", active=True)
    db.add_all([tp, tf]); db.flush()
    return p, f, tp, tf

def test_pick_task_respects_category_id_set(db_session):
    db = db_session; p, f, tp, tf = _mk(db)
    picks = {matchmaking.pick_task(db, category_ids={f.id}) for _ in range(8)}
    picks.discard(None)
    assert picks == {tf} or picks <= {tf}   # only fungi task eligible
```

- [ ] **Step 2: Run, verify fail** — `pytest -q tests/test_kingdom_filtering.py::test_pick_task_respects_category_id_set` — Expected: FAIL (unexpected kwarg / no filter).

- [ ] **Step 3: Extend `pick_task`.** In `app/matchmaking.py:48-78` add a `category_ids: set[int] | None = None` kwarg; where it currently does `stmt = stmt.where(Task.category_id == category_id)` (`:64-66`), branch: if `category_ids is not None:` use `.where(Task.category_id.in_(category_ids))` (empty set → no eligible tasks → returns None), elif `category_id is not None:` keep existing.

- [ ] **Step 4: Thread kingdom in the arena builders.** In `_build_comparison` / `_build_kwise_comparison`, compute `k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)` and pass `category_ids=k_ids` to `pick_task` (and in the kwise inline task query `app/main.py:397-399`, add `if k_ids is not None: stmt = stmt.where(Task.category_id.in_(k_ids))`). The explicit `?category=` selector still applies on top (intersect: if a single category is chosen, keep the existing single filter — a chosen category is always within a kingdom). Pass `request` through so `request.state.kingdom` is reachable (these builders are called from routes that have `request`).

- [ ] **Step 5: Scope `/api/meta`** so the category selector only lists categories in the active kingdom (when not `all`): filter the `Category` list by `kingdoms.category_ids_for_kingdom`.

- [ ] **Step 6: Run tests** — `pytest -q tests/test_kingdom_filtering.py tests/test_smoke_pages.py` — Expected: PASS.

- [ ] **Step 7: Commit** — `git commit -am "feat(kingdom): scope arena pool + matchmaking + /api/meta by kingdom"`

---

## Task 6: Kingdom filter — leaderboard + significance (on-the-fly BT)

**Files:**

- Modify: `app/service.py` — `_matches_for_scope` (`:174-239`, category filter `:205-206`), `_players_for_scope` (`:242-250`, `:245-248`), `verified_leaderboard_rows` (`:275-308`), `compute_significance` (`:571-612`) accept a category-id **set**.
- Modify: `app/main.py` — `_leaderboard_rows` (`:631-681`) and `significance_page` (`:949-983`) use the on-the-fly (uncached) path when a kingdom (≠ all) is active, since cached `Rating` rows are keyed by a single `category_id` and cannot represent a kingdom.
- Modify: `tests/test_kingdom_filtering.py` (leaderboard section).

**Interfaces:**

- Consumes: `kingdoms.category_ids_for_kingdom`.
- Produces: `_matches_for_scope(db, criterion_id, category_id=None, *, category_ids=None, verified_only=False)` and `_players_for_scope(db, category_id=None, *, category_ids=None)` — when `category_ids` given, filter `Task.category_id.in_(category_ids)`. `_leaderboard_rows`/`significance` return kingdom-scoped rows computed live.

- [ ] **Step 1: Write the failing test** — build two categories with generators/votes on each, assert a kingdom-scoped leaderboard only includes players with decisive matches in that kingdom. (Model the fixture on existing `tests/` leaderboard tests — reuse their vote-builder helpers; find them with `grep -rl "verified_leaderboard_rows\|_leaderboard_rows\|compute_significance" tests/`.)

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Add `category_ids` to `_matches_for_scope` + `_players_for_scope`** (`.in_(ids)` branch; keep single-`category_id` path).

- [ ] **Step 4: Route kingdom through leaderboard + significance.** In `_leaderboard_rows`/`significance_page`: `k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)`. If `k_ids is not None` (kingdom active), bypass the cached-`Rating` read and compute via `ranking.bradley_terry` over `_matches_for_scope(..., category_ids=k_ids)` + `_players_for_scope(category_ids=k_ids)` (same shape `verified_leaderboard_rows` already uses). If `all`, keep the existing cached path unchanged. Note the VLM judge board (`_judge_leaderboard_rows`, `:684-743`) is global-only today — for a kingdom scope, either hide it or compute live via `_judge_matches_for_scope` with an added category filter; the simpler acceptable scope for THIS pass: **hide the judge board when a single kingdom is active** (add a template conditional) and log that kingdom-scoped judge ranking is deferred.

- [ ] **Step 5: Run tests** — `pytest -q tests/test_kingdom_filtering.py tests/test_smoke_pages.py` — Expected: PASS.

- [ ] **Step 6: Commit** — `git commit -am "feat(kingdom): kingdom-scoped leaderboard + significance via on-the-fly BT"`

---

## Task 7: Kingdom filter — tasks, coverage, dataset-export, difficulty, benchmark, spotlight

**Files:**

- Modify: `app/main.py` (`tasks_page` `:989-1004`, `benchmark_page` task list `:1151-1213`, `difficulty_page` `tier_species` `:1256-1263`), `app/service.py` (`coverage_summary` `:677-772`), `app/difficulty.py` (`_accumulate_scorecard` `:129`), `app/dataset.py` (`build_preference_records` `:16-50`), `app/spotlight.py` (tag SPOTLIGHTS with a kingdom + filter the index).
- Modify: `tests/test_kingdom_filtering.py` (per-page assertions).

**Interfaces:**

- Consumes: `kingdoms.category_ids_for_kingdom`, `kingdoms.KINGDOM_OF`.
- Produces: each listed page filters its task/output set by the active kingdom (`all` == unchanged). Spotlight index filters by a `kingdom` field added to each `SPOTLIGHTS` dict (derive from the subject's category; add the field explicitly).

- [ ] **Step 1: Write failing tests** — for `tasks_page`: seed plants+fungi tasks, GET `/tasks?kingdom=fungi`, assert only fungi task titles present. Similar targeted asserts for coverage (task rows), difficulty (`tier_species`), benchmark (task picker options), spotlight index (only in-kingdom subjects listed).

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Inject the filter at each documented point:**
  - `tasks_page`: `select(Task)` → add `.where(Task.category_id.in_(k_ids))` when `k_ids is not None`.
  - `coverage_summary`: accept an optional `category_ids` and filter the active-tasks query (`app/service.py:707`); route passes `request.state.kingdom`.
  - `difficulty_page`: filter the `tier_species` join and pass `category_ids` into `tier_scorecard`/`paradigm_tier_scorecard`/`_accumulate_scorecard` (add a task-category join in `app/difficulty.py:129`).
  - `benchmark_page`: filter the `tasks` list + `_default_benchmark_task_id` candidates by `Task.category_id.in_(k_ids)`.
  - `build_preference_records`: optional kingdom filter on comparisons (used by `/api/export.json`).
  - `spotlight.py`: add `"kingdom": "<k>"` to each dict in `SPOTLIGHTS` (`:44-116`) via `KINGDOM_OF` of the subject's category; `spotlight_index` filters by `request.state.kingdom`.

- [ ] **Step 4: Run tests** — `pytest -q tests/test_kingdom_filtering.py tests/test_smoke_pages.py` — Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(kingdom): scope tasks/coverage/dataset/difficulty/benchmark/spotlight by kingdom"`

---

## Task 8: Animals → roadmap screen

**Files:**

- Create: `app/templates/_kingdom_roadmap.html`
- Modify: `app/main.py` — a small guard used by kingdom-scoped page routes: when `request.state.kingdom` is a **coming-soon** kingdom (no live tasks in its buckets), render the roadmap instead of the normal page for the data pages.
- Create: `tests/test_kingdom_roadmap.py`

**Interfaces:**

- Produces: `_kingdom_is_live(db, kingdom) -> bool` (True if the kingdom's categories have ≥1 active task); a roadmap render helper. Applied to Leaderboard, Arena, Difficulty, Significance, Benchmark, Coverage, Tasks, Dataset (the data screens). Home/Methodology/Submit/Spotlight are not gated.

- [ ] **Step 1: Write the failing test** — with no animal tasks seeded, GET `/leaderboard?kingdom=animals` returns 200 and contains "next on the roadmap"; with a plants task, `/leaderboard?kingdom=plants` renders the normal leaderboard (no roadmap copy).

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Build `_kingdom_roadmap.html`** — centered 820px screen: amber eyebrow, H1 "Animals are next on the roadmap" (exact copy from `<HANDOFF>` screen 14 / README §14), explanatory copy, 3-up status grid (✓ Plants live · ✓ Fungi live · ◷ Animals in sourcing), and a link back to `?kingdom=all`. Add `_kingdom_is_live` helper + gate the listed data routes: at the top of each, `if not _kingdom_is_live(db, request.state.kingdom): return TemplateResponse(request, "_kingdom_roadmap.html", {...})`.

- [ ] **Step 4: Run tests** — `pytest -q tests/test_kingdom_roadmap.py tests/test_smoke_pages.py` — Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(kingdom): coming-soon kingdoms route to roadmap screen"`

---

## Task 9: Home landing page (new) + move Arena to `/arena`

**Files:**

- Create: `app/templates/home.html`
- Modify: `app/main.py` — `GET /` → `home` (renders `home.html`); add `GET /arena` → the current `index` logic (renders `arena.html`).
- Modify: `tests/test_smoke_pages.py` — append `/arena` to `PUBLIC_PATHS`; create `tests/test_home_route.py`.

**Interfaces:**

- Consumes: shell + tokens; `matchmaking.total_votes`, generator/task/kingdom counts for the stats strip.
- Produces: `/` = landing, `/arena` = the vote loop. `arena.js` bootstrap is path-independent (fetches `/api/*`), so moving the template to `/arena` needs no JS change. Verify no internal template links point to `/` expecting the arena (grep; the sidebar "Arena" now points to `/arena`).

- [ ] **Step 1: Write `tests/test_home_route.py`** — GET `/` is 200 and contains the hero H1 substring "arena for" and the stats strip ("votes"); GET `/arena` is 200 and contains `#slot-a`/`#kwise-grid` (the arena still works at its new path).

- [ ] **Step 2: Run, verify fail** (arena markup currently at `/`, not `/arena`).

- [ ] **Step 3: Build `home.html`** to match `<HANDOFF>/screenshots/01-home.png` + README §1: hero (mono eyebrow "BIOLOGICAL 3D RECONSTRUCTION · BENCHMARK" in `--accent2`; H1 52px "The _life-sciences_ arena for **3D** generation" — "3D" with the stacked accent `text-shadow`, "life-sciences" in `--accent2`; muted lede; primary CTA → `/arena`, secondary → `/methodology`; stats strip with the breathing live-pulse dot (copy the `b3d-breathe` keyframe + dot markup from prototype `:207`, and the keyframe from `:28`) showing real counts; "Generators from" org row). Right column `.b3d-hero-viz`: reuse `window.Bio3DViewer.mount` with a sample GLB (or a static concentric-ring motif if no asset is wired — the ring motif is acceptable for v1; note it). Below hero: kingdom/feature cards. Wire the route in `main.py`.

- [ ] **Step 4: Run tests** — `pytest -q tests/test_home_route.py tests/test_smoke_pages.py` — Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(design): Home landing page + move Arena to /arena"`

---

## Tasks 10–21: Per-page re-skins

Each task: restyle the named template to its screenshot + README/DESIGN_SYSTEM spec using the token system, **preserving every JS/data contract listed in Global Constraints and the frontend recon**. Each ends with: boot + curl assertion of key markup, `pytest -q tests/test_smoke_pages.py`, and a commit. The screenshot is visual truth; the prototype HTML is the markup/copy source.

- [ ] **Task 10 — Arena** (`arena.html`, screenshot 02). Task strip (round ref thumb + kingdom chip + title + judge-on selects), two viewer stages (`#slot-a/#slot-b`, tall/darker with inner vignette + gold winner top-border after vote), floating pill vote bar (`.vote-bar .vote-btn` with A/B `--win` dot, Tie, Both-bad muted), reveal name pills + rank chips, "Next pair →". **Preserve every arena.js id/class** (Global Constraints) and the `#kwise-grid`/`#kwise-allbad` markup. Restyle viewer chrome via the `.viewer-*` classes. Commit.

- [ ] **Task 11 — Leaderboard** (`leaderboard.html`, screenshot 03). "How ranking works" info button, paradigm segmented tabs (Overall / Image→3D / Procedural / Scan capture — already present as `paradigm_options`), one-table system: rank medals top-3, `kind` chip, mono BT score (rank 1 = accent), CI whisker (`.ci-*` classes already exist — restyle), votes, provenance chips (`paper · code · data`). Keep the Trusted/Verified toggle + bias line. Scope pill by H1. Commit.

- [ ] **Task 12 — Models index + detail** (`models.html`, `model_detail.html` — NEW; screenshot 04). Route `GET /models` lists every `Generator` with a generated SVG icon, org, headline stats (BT score, votes, tasks) from existing rating/vote data; each row links to `GET /models/{slug}` (detail: ratings across tasks/kingdoms, sample outputs). Build the data in `main.py` from `Generator` + `service` helpers (reuse `_leaderboard_rows`/coverage stats; do not invent metrics). Add `/models` to smoke paths + `tests/test_models_page.py` (200 + a known generator name present). Commit.

- [ ] **Task 13 — Difficulty** (`difficulty.html`, screenshot 05). "Win-rate degradation" line chart (inline SVG, one line per method Easy→Moderate→Hard, top-3 labelled with a de-overlap nudge) built from the existing `gradient`/`scorecard` data; heatmap tier grid (`color-mix(win|amber|bad …)` cells) for the paradigm×tier tables; per-tier top-3 cards. All server-rendered SVG/markup. Commit.

- [ ] **Task 14 — Benchmark** (`benchmark.html`, screenshot 06). Cross-species agreement table (Spearman ± colored win/bad, mono), dual inspect viewer (recon vs GT, GT pane `--win` top-border) — **preserve the inline mount script `benchmark.html:66-92` element ids** and `window.Bio3DViewer.mount`. Metric grid (mono chamfer, PASS/FAIL pills). Commit.

- [ ] **Task 15 — Coverage** (`coverage.html`, screenshot 07). Paradigm stat-card grid (mono accent numerals from `by_paradigm`), generator-coverage table with firm/provisional confidence pills (`.cov-*` exist — restyle) + in-arena ✓ / `excluded`. Keep governance prose. Commit.

- [ ] **Task 16 — Significance** (`significance.html`, screenshot 08). Forest plot of BT estimates ± CI (inline SVG built from `sig.ranked`, dashed leader line, grey = separated / accent = overlapping), H2H `P(row>col)` matrix (`.matrix` cells exist — restyle to a heat scale), σ callout box. Commit.

- [ ] **Task 17 — Dataset** (`dataset.html`, screenshot 09). Stat-card row (reference specimens / kingdoms / model outputs / provenance types — compute from `Task`/`ModelOutput`/`ReconTask` counts), composition segmented bars by kingdom + by tier, inventory table with tier pills. Keep the releases list below. This adds a data builder in `main.py` (`dataset_composition(db)`); reuse counts, don't invent. Commit.

- [ ] **Task 18 — Tasks** (`tasks.html`, screenshot 10). Stat cards (live tasks / votes across tasks / paradigms), catalog table `T-01 …` with subject emoji + _italic latin_ + paradigm + tier pill + votes. Derive tier from `TaskDifficulty`, votes from existing counts. Commit.

- [ ] **Task 19 — Methodology** (`methodology.html`, screenshot 11). Numbered step cards 01–06 (Reference / Generate / Pair / Vote / Bradley–Terry / Rank — exact copy from screenshot) + the BT formula rendered as math (KaTeX not allowed offline; render as styled HTML/MathML or an inline-SVG formula — use HTML with `<sub>`/`<sup>` + mono). Keep the existing methodology prose lists below. Commit.

- [ ] **Task 20 — Submit** (`submit.html`, screenshot 12). Numbered step cards (01 Submit · 02 Calibrate · 03 Compete) + styled form (model name, organization, paradigm select, **kingdom select** (from `kingdoms.KINGDOMS`), output format select, file input). **Preserve the inline submit script `submit.html:29-48` element ids + `/api/submit` POST**. Commit.

- [ ] **Task 21 — Spotlight index + detail** (`spotlight_index.html`, `spotlight.html`, screenshot 13). Index: featured taxon cards with stage-gradient header + `featured` badge, taxonomy breadcrumb (`Kingdom › Order › Family › Genus`, mono faint), common name (`--accent2`) + _italic latin_ + description. Detail: restyle the existing `.spotlight-*` layout to tokens — **preserve every spotlight.js hook** (Global Constraints). Consider routing `spotlight.js`'s inline `<model-viewer>` through `window.Bio3DViewer.mount` for consistency (optional; if changed, verify the sticky `#live-viewer-slot` still loads). Commit.

---

## Task 22: Secondary pages inherit the shell + token restyle

**Files:** `admin.html`, `moderation.html`, `procedural.html`, `fidelity.html`, `trait_scorecard.html`, `terms.html`, `privacy.html`, `licenses.html`.

- [ ] **Step 1:** These already `{% extends "base.html" %}`, so they inherit the shell for free. Restyle their local component classes (`.admin-grid/.card`, `.mod-grid/.mod-card`, `.ranktable`, `.prose`, badges) to the token system — most inherit automatically once `.ranktable`/`.card`/`.prose` are tokenized. **Preserve `moderation.js`/inline-admin contracts** (`.mod-viewer`, `#admin-token`, `.token-mirror`). Terms/Privacy/Licenses are pure prose — just confirm `.prose` reads well in both themes.
- [ ] **Step 2:** Boot each (including admin with the test token) and assert 200. Add admin/moderation to a gated smoke check if convenient (they need the token) or verify manually via curl with `?token=test-token`.
- [ ] **Step 3: Commit** — `git commit -am "feat(design): secondary pages (admin/mod/legal/boards) token restyle"`

---

## Task 23: One-table + shared-component consolidation pass

**Files:** `app/static/style.css`.

- [ ] **Step 1:** DESIGN_SYSTEM.md prescribes "one table system" for all data-dense pages. Audit the restyled pages for a consistent `.ranktable`/`.board` treatment (uppercase mono micro-headers 10–10.5px `--faint`, hairline row rules, tabular-nums, hover `color-mix(in oklch, var(--accent) 7%, var(--panel2))`, semantic color only where meaningful). Fold per-page divergences into shared classes. Reconcile the two `.reference-panel` definitions (frontend recon flagged the duplicate at `style.css:~844` and `~1164`) — make the two intents explicit variants.
- [ ] **Step 2:** Verify no regressions — `pytest -q` (full suite) + boot every page.
- [ ] **Step 3: Commit** — `git commit -am "refactor(design): unify table system + reconcile duplicate component classes"`

---

## Task 24: Full-suite gate + cross-theme/mobile boot verification

**Files:** none (verification).

- [ ] **Step 1:** Run the FULL test suite (it is slow, ~160s — run detached if the shell caps at 120s): `pytest -q`. Expected: all pass (the animal-work baseline was 849 passed / 12 skipped; this plan only ADDS tests + backend kwargs, so the count only grows).
- [ ] **Step 2:** Boot the app and curl-assert each page in BOTH themes (theme is client-side, so assert the `data-theme` no-FOUC script + both palettes exist) and with each kingdom param (`?kingdom=plants|fungi|animals`), confirming 200 + roadmap for coming-soon kingdoms.
- [ ] **Step 3:** Grep for any leftover references to removed classes/ids (`.topbar`, `#nav-toggle`) to confirm nothing dangles.
- [ ] **Step 4:** No commit (verification task); proceed to the final whole-branch review.

---

## Self-Review (author checklist — completed)

- **Spec coverage:** all 13 screens + shell + tokens + kingdom filtering + roadmap are covered (Tasks 1–21); secondary pages Task 22. The three mockup-mandated additions (Home/Arena split, Models pages, richer Dataset) are explicit tasks (9, 12, 17).
- **Contracts:** every JS DOM contract from the frontend recon is enumerated in Global Constraints and repeated in the arena/benchmark/spotlight/moderation/submit tasks.
- **Backend correctness:** the single-`Rating.category_id` constraint (can't hold a kingdom set) is handled by the on-the-fly BT path (Task 6), matching the backend recon's recommendation. The judge board's global-only limitation is explicitly deferred (Task 6, Step 4).
- **Type consistency:** `category_ids: set[int] | None` (None == all) is the one added parameter shape across `pick_task`, `_matches_for_scope`, `_players_for_scope`, `coverage_summary`, difficulty helpers — consistent everywhere.
- **Test strategy:** backend tasks are real TDD; template tasks verify via boot + curl markup assertions + the smoke suite (CSS/visual can't be unit-asserted). This is a deliberate, stated compromise.
- **Deferred (logged, not silently dropped):** kingdom-scoped VLM judge board; wiring a real hero GLB (ring-motif fallback accepted for v1); Model detail depth; taxonomy drill-down below kingdom (README roadmap, explicitly out of scope).
