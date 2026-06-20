# Increment 3 — Viewer Affordances + Accessibility/Trust Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the core product (3D viewers) feel responsive and recoverable, fix the one real accessibility defect (colorblind-unsafe significance matrix), add focus/reduced-motion support, and remove trust-undermining chrome ("MVP" footer, Admin in public nav).

**Architecture:** Pure front-end pass — Jinja2 templates (`base.html`, `significance.html`), `style.css`, and the two viewer JS files (`viewer.js`, `arena.js`). No DB/model/route changes except adding a favicon static asset and a tiny `/favicon.ico` link. Verification is primarily **screenshot-based** via the Playwright harness `(.venv) shoot.py` (boots the app on a free port, screenshots key pages, reports console errors); narrow regression asserts are added in `tests/test_a11y.py` for the things that are cheap to assert server-side (footer copy, nav contents, favicon link, matrix legend present, matrix semantic classes).

**Tech Stack:** FastAPI + Jinja2, vanilla JS, 3Dmol.js (molecular), `<model-viewer>` (mesh), Playwright (Chromium) for screenshot verification, pytest + FastAPI TestClient.

## Global Constraints

- No build step — vanilla JS + server-rendered Jinja2 only. No new npm/bundler.
- Test env is the project venv: run pytest as `.venv/bin/python -m pytest`.
- Playwright screenshot harness: `PYTHONPATH=$(pwd) .venv/bin/python /home/mjarnold/.claude/jobs/3400ad8a/tmp/shoot.py <out_dir> <label>` — captures home/leaderboard/significance/methodology/tasks and reports console/page errors.
- ruff is a PostToolUse formatter; it can strip imports added before first use — add import + usage in the SAME edit and re-grep.
- **Accessibility framing correction (verified 2026-06-20):** `--muted #8b98a9` does NOT fail WCAG AA (measured 6.31:1 / 5.44:1 / 4.79:1 vs bg/panel/panel2; AA floor for normal text = 4.5:1). It DOES fail AAA (7:1) and is marginal on panel2. Treat the bump as AAA/small-text-headroom polish, NOT an AA-failure fix. New value `--muted: #a3b0c2` (8.42 / 7.26 / 6.38 — clears AAA on bg/panel, comfortably AA on panel2).
- The significance matrix's real defect IS confirmed: green `#4cc38a` vs red `#b9636b` is red-green-unsafe with no legend. Replace with a blue↔orange diverging scale (colorblind-safe), keep the numeric P value (the ultimate non-color encoding), add a directional glyph and a legend.
- Admin must remain reachable by direct URL (`/admin`) — only remove it from the public `<nav>`; do not gate the route.

---

### Task 1: Trust & chrome polish — drop "MVP", de-link Admin, add favicon

**Files:**

- Modify: `app/templates/base.html` (nav block lines 20-28, footer line 31-33, head lines 3-16)
- Create: `app/static/favicon.svg` (inline DNA-mark favicon)
- Test: `tests/test_a11y.py` (new)

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: nothing later tasks rely on (independent).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_a11y.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_footer_drops_mvp_label():
    html = client.get("/").text
    assert "· MVP" not in html
    assert "MVP" not in html.split("<footer")[1]  # nothing "MVP" in the footer


def test_admin_not_in_public_nav():
    html = client.get("/").text
    nav = html.split("<nav>")[1].split("</nav>")[0]
    assert ">Admin<" not in nav
    # but the admin route itself still works
    assert client.get("/admin").status_code == 200


def test_favicon_link_present_and_served():
    html = client.get("/").text
    assert 'rel="icon"' in html
    assert client.get("/static/favicon.svg").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_a11y.py -v`
Expected: FAIL — `· MVP` present, `>Admin<` present in nav, no favicon link.

- [ ] **Step 3: Create the favicon asset**

```svg
<!-- app/static/favicon.svg -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#0f1419"/>
  <g stroke="#4cc38a" stroke-width="2.2" fill="none" stroke-linecap="round">
    <path d="M11 6 C21 12, 11 20, 21 26"/>
    <path d="M21 6 C11 12, 21 20, 11 26"/>
  </g>
  <g stroke="#5b8def" stroke-width="1.6">
    <line x1="12.5" y1="9" x2="19.5" y2="9"/>
    <line x1="13" y1="16" x2="19" y2="16"/>
    <line x1="12.5" y1="23" x2="19.5" y2="23"/>
  </g>
</svg>
```

- [ ] **Step 4: Edit base.html — favicon link, drop MVP, de-link Admin**

In `<head>` (after the stylesheet `<link>` line 7) add:

```html
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
```

Replace the nav block (remove the Admin link line 27):

```html
<nav>
  <a href="/">Arena</a>
  <a href="/leaderboard">Leaderboard</a>
  <a href="/significance">Significance</a>
  <a href="/tasks">Tasks</a>
  <a href="/submit">Submit</a>
  <a href="/methodology">Methodology</a>
</nav>
```

Replace the footer:

```html
<footer class="foot">
  Anonymous pairwise comparison of biological 3D generations
</footer>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_a11y.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/templates/base.html app/static/favicon.svg tests/test_a11y.py
git commit -m "feat(chrome): favicon + drop MVP footer + de-link Admin from public nav"
```

---

### Task 2: Accessibility CSS — focus-visible, reduced-motion, muted AAA bump

**Files:**

- Modify: `app/static/style.css` (`:root` vars ~132-141; add new rules near top-level element styles ~157-185)
- Test: `tests/test_a11y.py` (extend)

**Interfaces:**

- Consumes: nothing.
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_a11y.py
def test_style_has_focus_and_reduced_motion_rules():
    css = client.get("/static/style.css").text
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    # muted bumped off the old marginal value
    assert "--muted: #8b98a9" not in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_a11y.py::test_style_has_focus_and_reduced_motion_rules -v`
Expected: FAIL — no `:focus-visible`, no `prefers-reduced-motion`, old muted present.

- [ ] **Step 3: Edit style.css**

Bump muted in `:root`:

```css
--muted: #a3b0c2;
```

After the `a:hover` rule (~line 163) add a global focus-visible style and reduced-motion guard:

```css
/* Keyboard focus — visible ring on all interactive elements */
a:focus-visible,
button:focus-visible,
select:focus-visible,
input:focus-visible,
textarea:focus-visible,
[tabindex]:focus-visible {
  outline: 2px solid var(--accent2);
  outline-offset: 2px;
  border-radius: 4px;
}

/* Respect reduced-motion: kill transitions/animations for users who ask */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    transition-duration: 0.001ms !important;
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_a11y.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Screenshot-verify focus ring**

Run: `PYTHONPATH=$(pwd) .venv/bin/python /home/mjarnold/.claude/jobs/3400ad8a/tmp/shoot.py /home/mjarnold/.claude/jobs/3400ad8a/tmp/after t2 && echo done`
Then visually confirm pages still render with no console errors (the harness reports them).

- [ ] **Step 6: Commit**

```bash
git add app/static/style.css tests/test_a11y.py
git commit -m "feat(a11y): focus-visible rings + reduced-motion guard + muted AAA bump"
```

---

### Task 3: Colorblind-safe significance matrix + legend

**Files:**

- Modify: `app/static/style.css` (`.mc-*` color classes lines 27-46)
- Modify: `app/templates/significance.html` (matrix cell class logic line 58; add legend after the matrix-wrap div line 64)
- Test: `tests/test_a11y.py` (extend)

**Interfaces:**

- Consumes: nothing.
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Write the failing test**

The matrix only renders when there is decisive data; seed + cast a few votes so the significance route returns a matrix, then assert the legend and a directional glyph are present.

```python
# append to tests/test_a11y.py
def test_significance_matrix_has_colorblind_legend():
    # cast a handful of decisive votes so a matrix renders
    import random

    random.seed(11)
    for i in range(12):
        nxt = client.get("/api/next?criterion=overall&category=all").json()
        client.post(
            "/api/vote?criterion=overall&category=all",
            json={"comparison_id": nxt["comparison_id"], "winner": "a" if i % 2 else "b"},
        )
    client.post("/admin/recompute", data={"token": "test-token"})
    html = client.get("/significance?criterion=overall&category=all").text
    assert "matrix-legend" in html  # legend block rendered
    assert "row clearly ahead" in html  # legend explains the scale in words
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_a11y.py::test_significance_matrix_has_colorblind_legend -v`
Expected: FAIL — no `matrix-legend`.

- [ ] **Step 3: Edit style.css — blue↔orange diverging scale (colorblind-safe)**

Replace the `.mc-hi/.mc-mid/.mc-na/.mc-lomid/.mc-lo` block (lines 27-42) with:

```css
.mc-hi {
  background: #2f6fed;
  color: #ffffff !important;
} /* row clearly beats col (>=0.95) — strong blue */
.mc-mid {
  background: #8fb3f4;
  color: #0c1117 !important;
} /* leans row (>=0.75) — light blue */
.mc-na {
  background: #2a3548;
  color: #e6edf3 !important;
} /* uncertain ~0.5 — neutral */
.mc-lomid {
  background: #e6b277;
  color: #0c1117 !important;
} /* leans col (<=0.25) — light orange */
.mc-lo {
  background: #c2701a;
  color: #ffffff !important;
} /* col clearly beats row (<=0.05) — strong orange */
```

Add legend styles after the `.mc-self` block (~line 46):

```css
.matrix-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: center;
  margin: -0.5rem 0 1.5rem;
  font-size: 0.78rem;
  color: var(--muted);
}
.matrix-legend .swatch {
  display: inline-block;
  width: 0.9rem;
  height: 0.9rem;
  border-radius: 3px;
  margin-right: 0.35rem;
  vertical-align: -0.12rem;
  border: 1px solid var(--border);
}
```

- [ ] **Step 4: Edit significance.html — directional glyph in cells + legend**

Replace the matrix cell `<td>` (line 58) so the cell carries a glyph + number (glyph is a non-color redundant encoding):

```html
<td
  class="mc {% if loop.index0 == ri %}mc-self{% elif p >= 0.95 %}mc-hi{% elif p >= 0.75 %}mc-mid{% elif p <= 0.05 %}mc-lo{% elif p <= 0.25 %}mc-lomid{% else %}mc-na{% endif %}"
>
  {% if loop.index0 != ri %}{% if p >= 0.75 %}▲ {% elif p <= 0.25 %}▼ {% endif
  %}{% endif %}{{ '%.2f'|format(p) }}
</td>
```

Add the legend right after the closing `</div>` of `matrix-wrap` (after line 64):

```html
<div class="matrix-legend">
  <span
    ><span class="swatch" style="background:#2f6fed"></span>▲ row clearly ahead
    (≥0.95)</span
  >
  <span
    ><span class="swatch" style="background:#8fb3f4"></span>leans row
    (≥0.75)</span
  >
  <span
    ><span class="swatch" style="background:#2a3548"></span>uncertain
    (~0.5)</span
  >
  <span
    ><span class="swatch" style="background:#e6b277"></span>leans column
    (≤0.25)</span
  >
  <span
    ><span class="swatch" style="background:#c2701a"></span>▼ column clearly
    ahead (≤0.05)</span
  >
  <span class="subtle">Cell = P(row ranks above column).</span>
</div>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_a11y.py -v`
Expected: PASS.

- [ ] **Step 6: Screenshot-verify the matrix**

Run: `PYTHONPATH=$(pwd) .venv/bin/python /home/mjarnold/.claude/jobs/3400ad8a/tmp/shoot.py /home/mjarnold/.claude/jobs/3400ad8a/tmp/after t3` (note: significance page needs votes; the harness seeds fresh with 0 votes so the matrix may be empty — verify on the dev DB instead, or accept the test as the gate). Confirm legend + blue/orange render.

- [ ] **Step 7: Commit**

```bash
git add app/static/style.css app/templates/significance.html tests/test_a11y.py
git commit -m "feat(a11y): colorblind-safe blue/orange significance matrix + glyph + legend"
```

---

### Task 4: Viewer affordances — loading state, drag hint, asset-failure fallback

**Files:**

- Modify: `app/static/viewer.js` (mount/mountMesh/mountMolecular)
- Modify: `app/static/style.css` (add `.viewer-hint`, `.viewer-loading`, `.viewer-error` near `.viewer-slot` ~274-288)
- Test: screenshot-verified via Playwright (loading→loaded, hint visible, broken-asset fallback). A dedicated Playwright assertion script is added under the job tmp dir; no pytest unit test (DOM/canvas behavior is not unit-testable without a browser).

**Interfaces:**

- Consumes: `window.Bio3DViewer.mount(slot, asset)` (existing).
- Produces: same `mount` signature, now returning the format string AND rendering loading/hint/error overlays inside the slot.

- [ ] **Step 1: Add CSS affordance styles**

After `.viewer-slot canvas` (~line 288) add:

```css
.viewer-hint {
  position: absolute;
  left: 50%;
  bottom: 8px;
  transform: translateX(-50%);
  background: rgba(15, 20, 25, 0.72);
  color: #c8d2de;
  font-size: 0.72rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.4s;
  z-index: 3;
}
.viewer-slot:hover .viewer-hint {
  opacity: 1;
}
.viewer-loading,
.viewer-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 0.5rem;
  color: var(--muted);
  font-size: 0.85rem;
  z-index: 2;
  text-align: center;
  padding: 0 1rem;
}
.viewer-error {
  color: #e6b277;
}
.viewer-spinner {
  width: 26px;
  height: 26px;
  border: 3px solid rgba(91, 141, 239, 0.25);
  border-top-color: var(--accent2);
  border-radius: 50%;
  animation: viewer-spin 0.8s linear infinite;
}
@keyframes viewer-spin {
  to {
    transform: rotate(360deg);
  }
}
```

- [ ] **Step 2: Rewrite viewer.js with loading/hint/error overlays**

Full new `app/static/viewer.js`:

```javascript
// Shared 3D viewer registry — mount a renderer into a slot element by asset format.
// model-viewer for GLB/GLTF meshes; 3Dmol.js for PDB/mmCIF molecular structures.
// Adds loading spinner, drag-to-rotate hint, and an asset-failure fallback so the
// core product never silently shows an empty box.
(function () {
  const MESH = new Set(["glb", "gltf"]);
  const MOL = new Set(["pdb", "cif", "mmcif", "ent", "sdf", "mol"]);

  function spinner(slot, label) {
    const d = document.createElement("div");
    d.className = "viewer-loading";
    d.innerHTML =
      '<div class="viewer-spinner"></div><span>' + label + "</span>";
    slot.appendChild(d);
    return d;
  }

  function hint(slot, text) {
    const h = document.createElement("div");
    h.className = "viewer-hint";
    h.textContent = text;
    slot.appendChild(h);
  }

  function failed(slot, msg) {
    slot.innerHTML = "";
    const d = document.createElement("div");
    d.className = "viewer-error";
    d.innerHTML = "⚠️ <span>" + msg + "</span>";
    slot.appendChild(d);
  }

  function mountMesh(slot, asset) {
    const loading = spinner(slot, "Loading model…");
    const mv = document.createElement("model-viewer");
    mv.setAttribute("camera-controls", "");
    mv.setAttribute("touch-action", "pan-y");
    mv.setAttribute("shadow-intensity", "1");
    mv.setAttribute("exposure", "1.0");
    mv.setAttribute("src", asset.url);
    mv.style.width = "100%";
    mv.style.height = "100%";
    mv.addEventListener("load", () => {
      loading.remove();
      hint(slot, "drag to rotate · scroll to zoom");
    });
    mv.addEventListener("error", () => failed(slot, "Model failed to load"));
    slot.appendChild(mv);
  }

  async function mountMolecular(slot, asset, fmt) {
    const loading = spinner(slot, "Loading structure…");
    try {
      const res = await fetch(asset.url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const text = await res.text();
      const viewer = window.$3Dmol.createViewer(slot, {
        backgroundColor: "0x131a24",
      });
      let modelType = "pdb";
      if (fmt === "cif" || fmt === "mmcif") modelType = "cif";
      else if (fmt === "sdf" || fmt === "mol") modelType = "sdf";
      viewer.addModel(text, modelType);
      viewer.setStyle(
        {},
        {
          stick: { radius: 0.15 },
          sphere: { scale: 0.28 },
          cartoon: { color: "spectrum" },
        },
      );
      viewer.zoomTo();
      viewer.render();
      loading.remove();
      hint(slot, "drag to rotate · scroll to zoom");
    } catch (e) {
      failed(slot, "Structure failed to load");
    }
  }

  // Mount the right viewer; returns the resolved format string.
  function mount(slot, asset) {
    slot.innerHTML = ""; // tear down any previous viewer
    const fmt = (asset.format || "glb").toLowerCase();
    if (MOL.has(fmt)) mountMolecular(slot, asset, fmt);
    else if (MESH.has(fmt)) mountMesh(slot, asset);
    else failed(slot, "Unsupported format: " + fmt);
    return fmt;
  }

  window.Bio3DViewer = { mount, MESH_FORMATS: MESH, MOLECULAR_FORMATS: MOL };
})();
```

- [ ] **Step 3: Screenshot-verify loaded state + hint + fallback**

Write `/home/mjarnold/.claude/jobs/3400ad8a/tmp/viewer_check.py` that boots the app, opens `/`, waits for `model-viewer` load, asserts `.viewer-hint` exists and `.viewer-loading` is gone, screenshots; then injects a broken asset URL via `Bio3DViewer.mount(slot,{format:'glb',url:'/static/does-not-exist.glb'})` and asserts `.viewer-error` appears.

Run: `PYTHONPATH=$(pwd) .venv/bin/python /home/mjarnold/.claude/jobs/3400ad8a/tmp/viewer_check.py`
Expected: prints `HINT_OK True`, `ERROR_FALLBACK_OK True`.

- [ ] **Step 4: Run the full suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/static/viewer.js app/static/style.css
git commit -m "feat(viewer): loading spinner + drag-to-rotate hint + asset-failure fallback"
```

---

## Final verification (controller, before merge)

- [ ] Re-run the full suite ≥2× (pre-existing flakes surface at merge time — process lesson from Inc2): `.venv/bin/python -m pytest -q` twice; both green.
- [ ] ruff clean: `.venv/bin/ruff check app tests`.
- [ ] Screenshot the full after-set and eyeball home/leaderboard/significance vs the baseline: `PYTHONPATH=$(pwd) .venv/bin/python /home/mjarnold/.claude/jobs/3400ad8a/tmp/shoot.py /home/mjarnold/.claude/jobs/3400ad8a/tmp/after final`.
- [ ] Whole-branch review (requesting-code-review) before ff-merge to master; gate the merge with `&&`/`set -e`.
- [ ] Update `docs/audits/2026-06-20-field-audit.md` (mark D1/D2/D4 items done, correct the muted-AA claim) and the memory roadmap.
