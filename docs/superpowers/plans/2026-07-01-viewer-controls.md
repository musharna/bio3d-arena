# Viewer Controls (Reset + Fullscreen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-slot overlay toolbar (⟳ Reset view + ⛶ Fullscreen) to every arena 3D viewer — both mesh (`<model-viewer>`) and molecular (3Dmol) — so a voter can re-frame a spun-off model and inspect one model full-screen.

**Architecture:** Pure frontend. In `app/static/viewer.js` add `addControls(slot)` (builds the toolbar), `toggleFullscreen(slot)`, per-slot `_resetView`/`_onResize` closures set by each mount, and ONE module-level `fullscreenchange` listener. `app/static/style.css` gets the toolbar styling (hover-reveal desktop, always-visible touch). No server change, no `arena.js` change, no new deps.

**Tech Stack:** Vanilla JS + Google `<model-viewer>` 3.5.0 camera API (`cameraOrbit`/`fieldOfView`/`cameraTarget` setters + `jumpCameraToGoal()`) + 3Dmol (`zoomTo`/`resize`/`render`) + the Fullscreen API. Files: `app/static/viewer.js`, `app/static/style.css`.

## Global Constraints

- **Pure frontend:** only `app/static/viewer.js` and `app/static/style.css` change. No server code, no `arena.js`, no new dependencies.
- **Both modalities** get the toolbar (mesh + molecular); the failed/unsupported-format paths render **no** toolbar.
- **Only one `fullscreenchange` listener**, added once at IIFE init — never per-mount (no listener leak on rapid re-mount).
- **Reset must not propagate across the synced pair:** the mesh reset writes the camera programmatically → model-viewer fires `camera-change` with `detail.source === "none"` → the existing `syncPair` ignores it. Do not change `syncPair`.
- **Slot property hygiene:** `mount()` does `slot.innerHTML = ""` but does NOT clear the `slot._resetView`/`slot._onResize`/`slot._molViewer` properties (they live on the element, not the DOM subtree). Each mount MUST reassign `_resetView` and set `_onResize` (to the resize closure for molecular, to `null` for mesh) so a mesh mount can't inherit a torn-down molecular viewer's resize closure.
- **Verified model-viewer 3.5.0 defaults:** `camera-orbit` `"0deg 75deg auto"`, `field-of-view` `"auto"`, `camera-target` `"auto auto auto"`. `getCameraOrbit()` returns radians (default phi = 75° = 1.30899 rad, theta = 0).

---

### Task 1: Toolbar + reset/fullscreen behavior in `viewer.js` + CSS + pytest smoke

**Files:**

- Modify: `app/static/viewer.js` (add `addControls`, `toggleFullscreen`, module `fullscreenchange` listener; set closures in `mountMesh` + `mountMolecular`)
- Modify: `app/static/style.css` (add `.viewer-controls` / `.viewer-ctl` rules + touch always-visible rule)
- Test: `tests/test_viewer_controls.py` (create)

**Interfaces:**

- Produces (internal to the IIFE, not exported): `addControls(slot)` appends `<div class="viewer-controls">` with two `<button class="viewer-ctl">`; `toggleFullscreen(slot)` enters/exits fullscreen on `slot`; each mount sets `slot._resetView` (reset closure) and `slot._onResize` (molecular resize closure or `null`); molecular also sets `slot._molViewer`.
- `window.Taxon3DViewer` export is unchanged (`mount`, `syncPair`, `MESH_FORMATS`, `MOLECULAR_FORMATS`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_viewer_controls.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_viewer_js_has_controls():
    vjs = client.get("/static/viewer.js").text
    # toolbar builder + fullscreen toggle exist
    assert "function addControls" in vjs
    assert "function toggleFullscreen" in vjs
    # toolbar markup + a11y labels
    assert "viewer-controls" in vjs
    assert "viewer-ctl" in vjs
    assert "Reset view" in vjs
    assert "Fullscreen" in vjs
    # reset closure + molecular resize hook + fullscreen API + single listener
    assert "_resetView" in vjs
    assert "_onResize" in vjs
    assert "requestFullscreen" in vjs
    assert "fullscreenchange" in vjs


def test_style_css_has_control_rules():
    css = client.get("/static/style.css").text
    assert ".viewer-controls" in css
    assert ".viewer-ctl" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_viewer_controls.py -v`
Expected: FAIL — `assert "function addControls" in vjs` (not yet added).

- [ ] **Step 3: Add `addControls` + `toggleFullscreen` + the module `fullscreenchange` listener to `viewer.js`**

In `app/static/viewer.js`, immediately AFTER the `failed(slot, msg)` function (ends `}` around line 36) and BEFORE `function mountMesh(slot, asset) {`, add:

```js
function addControls(slot) {
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
  slot.appendChild(bar);
}

function toggleFullscreen(slot) {
  if (document.fullscreenElement) document.exitFullscreen();
  else if (slot.requestFullscreen) slot.requestFullscreen();
}

// Single module-level fullscreen listener (added once). On enter, the slot is
// document.fullscreenElement; on exit it is null, so we keep the previously-
// fullscreen slot to resize it back. Only molecular slots have _onResize (mesh
// auto-resizes → null). try/catch guards a slot torn down while fullscreen.
let fsSlot = null;
document.addEventListener("fullscreenchange", () => {
  const active = document.fullscreenElement;
  const target = active || fsSlot;
  if (target && target._onResize) {
    try {
      target._onResize();
    } catch (e) {
      /* stale viewer — ignore */
    }
  }
  fsSlot = active;
});
```

- [ ] **Step 4: Set the reset closure + add the toolbar in `mountMesh`**

In `app/static/viewer.js`, in `mountMesh`, the final line is `slot.appendChild(mv);`. Immediately AFTER it, add:

```js
slot._resetView = () => {
  mv.cameraOrbit = "0deg 75deg auto";
  mv.fieldOfView = "auto";
  mv.cameraTarget = "auto auto auto";
  mv.jumpCameraToGoal();
};
slot._onResize = null; // model-viewer auto-resizes; clear any stale molecular closure
addControls(slot);
```

- [ ] **Step 5: Set the reset/resize closures + add the toolbar in `mountMolecular`**

In `app/static/viewer.js`, in `mountMolecular`, inside the `try` block, the successful path currently ends:

```js
viewer.zoomTo();
viewer.render();
loading.remove();
hint(slot, "drag to rotate · scroll to zoom");
```

Immediately AFTER the `hint(slot, ...)` line (still inside the `try`, so the stale-gen `return`s above already guard it), add:

```js
slot._molViewer = viewer;
slot._resetView = () => {
  viewer.zoomTo();
  viewer.render();
};
slot._onResize = () => {
  viewer.resize();
  viewer.render();
};
addControls(slot);
```

- [ ] **Step 6: Add the toolbar CSS to `style.css`**

In `app/static/style.css`, find the `.viewer-hint` block (the drag-to-rotate hint, `position: absolute`, bottom-center, `z-index: 3`, revealed on `.viewer-slot:hover`). Immediately AFTER the `.viewer-hint` rules (including its `:hover` reveal rule), add:

```css
.viewer-controls {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 6px;
  z-index: 4;
  opacity: 0.55;
  transition: opacity 0.15s ease;
  pointer-events: auto;
}
.viewer-slot:hover .viewer-controls {
  opacity: 1;
}
.viewer-ctl {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  font-size: 15px;
  line-height: 1;
  color: #e8eef5;
  background: rgba(15, 20, 25, 0.72);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.viewer-ctl:hover,
.viewer-ctl:focus {
  background: rgba(30, 40, 52, 0.92);
  outline: none;
}
/* Touch devices have no hover — keep the toolbar always tappable. */
@media (hover: none) {
  .viewer-controls {
    opacity: 1;
  }
}
```

(If `--border` is not a defined CSS custom property in this stylesheet, use `rgba(255, 255, 255, 0.18)` instead — confirm against the existing `.viewer-slot` / `.viewer-hint` rules, which already reference the project's border color.)

- [ ] **Step 7: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_viewer_controls.py -v` → both tests PASS.
Run: `.venv/bin/pytest -q` → no regressions (no server change; baseline was 555 passed / 8 skipped).
**Never** set `BIO3D_DATABASE_URL=study` when running pytest (it wipes the study DB).

- [ ] **Step 8: Commit**

```bash
git add app/static/viewer.js app/static/style.css tests/test_viewer_controls.py
git commit -m "feat(viewer): per-slot reset + fullscreen toolbar (mesh + molecular)"
```

---

### Task 2: Playwright behavioral verification

**Files:**

- Create: `scripts/verify_viewer_controls.py`

**Interfaces:**

- Consumes: a running instance on :8099 whose current pair is GLB/GLB (seed data is procedural GLBs). Asserts Reset returns the camera to model-viewer's default framing and Fullscreen enters/exits on the slot. Mirrors the style of `scripts/verify_synced_rotation.py` (playwright interpreter, `--no-sandbox`, graceful SKIP on a non-mesh pair, exit 0/1).

- [ ] **Step 1: Write the verification script**

```python
# scripts/verify_viewer_controls.py
"""Verify the per-slot viewer toolbar on a GLB/GLB pair: Reset returns A's camera to
model-viewer's default framing (theta approx 0, phi approx 75deg); Fullscreen enters
then exits on A's .viewer-slot. Boot the app on :8099 first, run with a playwright
interpreter. Exit 0 on pass (or a graceful SKIP if the shown pair is not mesh)."""

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
pw = sync_playwright().start()
b = pw.chromium.launch(args=["--no-sandbox"])
p = b.new_page(viewport={"width": 1440, "height": 1000})
p.goto(BASE + "/", wait_until="networkidle", timeout=20000)
fails = []

ready = p.evaluate(
    """async () => {
      const a = document.querySelector('#slot-a model-viewer');
      const b = document.querySelector('#slot-b model-viewer');
      if (!a || !b) return 'not-mesh-pair';
      const wait = (mv) => mv.loaded ? Promise.resolve()
        : new Promise(r => mv.addEventListener('load', r, {once:true}));
      await Promise.race([Promise.all([wait(a), wait(b)]), new Promise(r => setTimeout(r, 8000))]);
      return (a.loaded && b.loaded) ? 'ready' : 'load-timeout';
    }"""
)
if ready == "not-mesh-pair":
    print("SKIP: current pair is not GLB/GLB — reload to get a mesh pair")
    b.close()
    pw.stop()
    sys.exit(0)
if ready != "ready":
    fails.append(f"model-viewers not loaded: {ready}")

# Toolbar exists: each .viewer-slot has two .viewer-ctl buttons.
counts = p.evaluate(
    """() => {
      const a = document.querySelector('#slot-a').querySelectorAll('.viewer-ctl').length;
      const b = document.querySelector('#slot-b').querySelectorAll('.viewer-ctl').length;
      return {a, b};
    }"""
)
if counts["a"] != 2 or counts["b"] != 2:
    fails.append(f"expected 2 .viewer-ctl per slot, got A={counts['a']} B={counts['b']}")

# Reset: spin A off default, click its Reset button (first .viewer-ctl), assert default framing.
reset = p.evaluate(
    """() => {
      const mv = document.querySelector('#slot-a model-viewer');
      mv.cameraOrbit = '1.4rad 0.9rad 3m'; mv.jumpCameraToGoal();
      const moved = mv.getCameraOrbit();
      document.querySelector('#slot-a .viewer-ctl').click();  // Reset (first button)
      const o = mv.getCameraOrbit();
      return { movedTheta: moved.theta, movedPhi: moved.phi, theta: o.theta, phi: o.phi };
    }"""
)
# default: theta 0 rad, phi 75deg = 1.309 rad. Tolerance for rounding.
if abs(reset["theta"]) > 0.05 or abs(reset["phi"] - 1.309) > 0.05:
    fails.append(f"Reset did not restore default framing: theta={reset['theta']} phi={reset['phi']}")

# Fullscreen: click A's second .viewer-ctl → A's .viewer-slot becomes fullscreenElement; click again → null.
enter = p.evaluate(
    """async () => {
      const slot = document.querySelector('#slot-a');
      slot.querySelectorAll('.viewer-ctl')[1].click();  // Fullscreen (second button)
      await new Promise(r => setTimeout(r, 300));
      return document.fullscreenElement === slot;
    }"""
)
if not enter:
    fails.append("Fullscreen did not enter on #slot-a")
exit_ok = p.evaluate(
    """async () => {
      document.querySelector('#slot-a').querySelectorAll('.viewer-ctl')[1].click();
      await new Promise(r => setTimeout(r, 300));
      return document.fullscreenElement === null;
    }"""
)
if not exit_ok:
    fails.append("Fullscreen did not exit")

b.close()
pw.stop()
print("FAILURES:", fails if fails else "NONE — viewer-controls checks pass")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Boot + run**

Boot the app on :8099 (background, seeded GLB data — same procedure as the synced-rotation verify), then run with the playwright-enabled interpreter:

```bash
<playwright-python> scripts/verify_viewer_controls.py
```

Expected: `FAILURES: NONE — viewer-controls checks pass` (or a clear `SKIP` if the shown pair is not GLB/GLB — reload for a mesh pair). Stop the server after.

Note on fullscreen in headless Chromium: a synthetic `.click()` in `page.evaluate` is NOT a trusted gesture and may reject `requestFullscreen`. If the enter step fails on that basis (not a logic bug), drive the click through Playwright's trusted `page.click('#slot-a .viewer-ctl >> nth=1')` instead of an in-page `.click()`; keep the `document.fullscreenElement` assertions.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_viewer_controls.py
git commit -m "test(viewer): Playwright verify for reset + fullscreen toolbar"
```

---

## Self-Review

**Spec coverage:**

- Per-slot toolbar with Reset + Fullscreen, both modalities → `addControls` called at end of both mounts (Task 1 Steps 4–5). ✓
- Reset restores default framing (mesh camera reset / molecular `zoomTo`) → `_resetView` closures (Task 1 Steps 4–5). ✓
- Per-slot fullscreen + single `fullscreenchange` listener resizing molecular viewers → `toggleFullscreen` + module listener (Task 1 Step 3). ✓
- Reset does not propagate across synced pair → mesh reset fires `source:"none"`; `syncPair` untouched (Global Constraints; verified indirectly — synced-rotation tests still green). ✓
- Toolbar only on successful mount → `addControls` not called on `failed(...)` paths; `failed()` does `innerHTML=""` removing any toolbar. ✓
- Fresh per pair + no listener leak → `mount()`'s `innerHTML=""` drops the toolbar DOM; the one listener is module-level; closures reassigned each mount incl. `_onResize=null` on mesh (Global Constraints; Task 1 Steps 4–5). ✓
- Hover-reveal desktop / always-visible touch → CSS `.viewer-slot:hover` + `@media (hover: none)` (Task 1 Step 6). ✓
- Testing: pytest markup smoke + Playwright behavioral → Task 1 test + Task 2. ✓

**Placeholder scan:** no TBD/TODO; complete code in every step. Task 2 `<playwright-python>` = the playwright-enabled interpreter used for the synced-rotation verify; not a logic placeholder. The `--border` fallback and trusted-click fallback are explicit adjust-on-contact branches, not gaps.

**Type/name consistency:** `addControls(slot)`, `toggleFullscreen(slot)`, `slot._resetView`, `slot._onResize`, `slot._molViewer`, class names `viewer-controls`/`viewer-ctl`, aria-labels `Reset view`/`Fullscreen` — identical across viewer.js, the pytest smoke asserts, and the Playwright selectors. `#slot-a`/`#slot-b` match `arena.html`'s `id="slot-a"`/`id="slot-b"`. model-viewer default phi = 1.309 rad used consistently in the Playwright assertion.

**Adjust-on-contact:**

- Confirm `/static/style.css` is the served path and that `--border` (or the project's border custom property) exists; fall back to the literal rgba if not (Task 1 Step 6 note).
- Confirm the `mountMesh` `slot.appendChild(mv);` line and the `mountMolecular` `hint(slot, ...)` line are the exact insertion anchors against live `viewer.js` (line numbers, not content, may have shifted).
- Headless-fullscreen trusted-gesture fallback documented in Task 2 Step 2.
