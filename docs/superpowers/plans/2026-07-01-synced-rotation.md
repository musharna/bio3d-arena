# Synced A/B Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock the two arena viewers' cameras together for mesh (GLB/GLTF) pairs — a user rotate/zoom/pan on either mirrors the exact view to the other — so morphology is compared at the identical angle.

**Architecture:** Add `syncPair(slotA, slotB)` to the shared viewer registry (`viewer.js`); `arena.js` calls it after mounting each pair. Sync is `<model-viewer>`↔`<model-viewer>` only; it propagates only user-initiated `camera-change` events (feedback-safe, no flag). Molecular / mixed / failed pairs no-op → independent rotation.

**Tech Stack:** Vanilla JS + Google `<model-viewer>` 3.5.0 camera API. Files: `app/static/viewer.js`, `app/static/arena.js`. No server change, no deps.

## Global Constraints

- **Feedback-safe:** only propagate `camera-change` when `event.detail.source === "user-interaction"`. Programmatic writes (`jumpCameraToGoal`) fire `source: "none"` → ignored → no bounce. No mutex/flag needed.
- **Mesh-pairs only:** if either slot has no `<model-viewer>` (molecular via 3Dmol, mixed, or a load failure), `syncPair` is a **no-op that never throws**.
- **Fresh per pair:** `render()` re-mounts both viewers and re-calls `syncPair` each pair; old elements (with listeners) are discarded by `mount()`'s teardown — no leak, no cross-pair sync.
- **Verified model-viewer 3.5.0 API:** read `getCameraOrbit()`/`getCameraTarget()` (have `.toString()`), `getFieldOfView()` (number, deg); write `cameraOrbit`/`cameraTarget` (strings), `fieldOfView = n + "deg"`, then `jumpCameraToGoal()`.
- Pure frontend; the P0 mobile + onboarding behavior in `arena.js`/`render()` is untouched except the one added `syncPair` call.

---

### Task 1: `syncPair` in `viewer.js` + wire it in `arena.js`

**Files:**

- Modify: `app/static/viewer.js` (add `syncPair`; export it)
- Modify: `app/static/arena.js` (call `syncPair` in `render()`)
- Test: `tests/test_synced_rotation.py` (create)

**Interfaces:**

- Produces: `window.Taxon3DViewer.syncPair(slotA, slotB)` — wires bidirectional user-driven camera sync between the `<model-viewer>` in each slot (no-op if either is absent).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synced_rotation.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_syncpair_defined_and_wired():
    vjs = client.get("/static/viewer.js").text
    assert "function syncPair" in vjs
    assert "syncPair" in vjs and "Taxon3DViewer" in vjs  # exported
    # gated on user-interaction (feedback-safe)
    assert "user-interaction" in vjs
    ajs = client.get("/static/arena.js").text
    assert "syncPair(" in ajs  # arena.js invokes it
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_synced_rotation.py -v`
Expected: FAIL — `assert "function syncPair" in vjs`.

- [ ] **Step 3: Add `syncPair` to `viewer.js`**

In `app/static/viewer.js`, immediately BEFORE the `window.Taxon3DViewer = { … }` line (currently line ~108), add:

```js
// Lock two mesh viewers' cameras together (side-by-side comparison at the same angle).
// No-op unless BOTH slots hold a <model-viewer> (molecular/mixed/failed pairs rotate freely).
// Only user-initiated camera-change events propagate — programmatic writes fire source
// "none" and are ignored, so applying A→B never bounces back (no mutex needed).
function syncPair(slotA, slotB) {
  const a = slotA && slotA.querySelector("model-viewer");
  const b = slotB && slotB.querySelector("model-viewer");
  if (!a || !b) return;
  function copyCam(src, dst) {
    dst.cameraOrbit = src.getCameraOrbit().toString();
    dst.cameraTarget = src.getCameraTarget().toString();
    dst.fieldOfView = src.getFieldOfView() + "deg";
    dst.jumpCameraToGoal();
  }
  a.addEventListener("camera-change", (e) => {
    if (e.detail && e.detail.source === "user-interaction") copyCam(a, b);
  });
  b.addEventListener("camera-change", (e) => {
    if (e.detail && e.detail.source === "user-interaction") copyCam(b, a);
  });
}
```

Then change the export line from `window.Taxon3DViewer = { mount, MESH_FORMATS: MESH, MOLECULAR_FORMATS: MOL };` to:

```js
window.Taxon3DViewer = {
  mount,
  syncPair,
  MESH_FORMATS: MESH,
  MOLECULAR_FORMATS: MOL,
};
```

- [ ] **Step 4: Call it in `arena.js`**

In `app/static/arena.js`, in `render(data)`, AFTER the two `Taxon3DViewer.mount(...)` calls that set `el("fmt-a")` / `el("fmt-b")` (the two viewers are mounted there), add — before the existing `setAB("a")` / `setStatus("")` lines:

```js
window.Taxon3DViewer.syncPair(el("slot-a"), el("slot-b"));
```

(Confirm the exact spot: it must run after BOTH `mount` calls so both `<model-viewer>`s exist. The anchor is the `el("fmt-b") = window.Taxon3DViewer.mount(...)` line; insert right after it.)

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_synced_rotation.py -v` → PASS.
Run: `.venv/bin/pytest -q` → no regressions (no server change).

- [ ] **Step 6: Commit**

```bash
git add app/static/viewer.js app/static/arena.js tests/test_synced_rotation.py
git commit -m "feat(viewer): synced A/B camera for mesh pairs (feedback-safe)"
```

---

### Task 2: Playwright behavioral verification

**Files:**

- Create: `scripts/verify_synced_rotation.py`

**Interfaces:**

- Consumes: a running instance on :8099 whose current pair is GLB/GLB (the seed data is procedural GLBs). Asserts A-drag mirrors to B and the feedback gate holds.

- [ ] **Step 1: Write the verification script**

```python
# scripts/verify_synced_rotation.py
"""Verify synced A/B rotation on a GLB/GLB pair: a user-source camera-change on A mirrors A's
camera onto B; a programmatic-source event does NOT (feedback gate). Boot the app on :8099 first,
run with a playwright interpreter. Exit 0 on pass."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
pw = sync_playwright().start()
b = pw.chromium.launch(args=["--no-sandbox"])
p = b.new_page(viewport={"width": 1440, "height": 1000})
p.goto(BASE + "/", wait_until="networkidle", timeout=20000)
fails = []

# Wait until both slots have a loaded <model-viewer> (GLB pair). If not a mesh/mesh pair, skip.
ready = p.evaluate(
    """async () => {
      const a = document.querySelector('#slot-a model-viewer');
      const b = document.querySelector('#slot-b model-viewer');
      if (!a || !b) return 'not-mesh-pair';
      const wait = (mv) => mv.loaded ? Promise.resolve() : new Promise(r => mv.addEventListener('load', r, {once:true}));
      await Promise.race([Promise.all([wait(a), wait(b)]), new Promise(r => setTimeout(r, 8000))]);
      return (a.loaded && b.loaded) ? 'ready' : 'load-timeout';
    }"""
)
if ready == "not-mesh-pair":
    print("SKIP: current pair is not GLB/GLB (nothing to sync) — re-run when a mesh pair is shown")
    b.close(); pw.stop(); sys.exit(0)
if ready != "ready":
    fails.append(f"model-viewers not loaded: {ready}")

# Set A's camera, fire a USER-interaction camera-change, assert B mirrors A.
result = p.evaluate(
    """() => {
      const a = document.querySelector('#slot-a model-viewer');
      const b = document.querySelector('#slot-b model-viewer');
      a.cameraOrbit = '0.7rad 1.1rad 2.5m'; a.jumpCameraToGoal();
      a.dispatchEvent(new CustomEvent('camera-change', {detail:{source:'user-interaction'}}));
      const aOrbit = a.getCameraOrbit().toString();
      const bOrbit = b.getCameraOrbit().toString();
      return {aOrbit, bOrbit};
    }"""
)
if result["aOrbit"] != result["bOrbit"]:
    fails.append(f"B did not mirror A: A={result['aOrbit']} B={result['bOrbit']}")

# Feedback gate: a programmatic-source event on A must NOT re-copy (B unchanged from a fresh A change).
gate = p.evaluate(
    """() => {
      const a = document.querySelector('#slot-a model-viewer');
      const b = document.querySelector('#slot-b model-viewer');
      const before = b.getCameraOrbit().toString();
      a.cameraOrbit = '1.4rad 0.9rad 3m'; a.jumpCameraToGoal();
      a.dispatchEvent(new CustomEvent('camera-change', {detail:{source:'none'}}));
      const after = b.getCameraOrbit().toString();
      return before === after;  // true = gate held (B did NOT follow a programmatic event)
    }"""
)
if not gate:
    fails.append("feedback gate failed: B followed a programmatic-source camera-change")

b.close(); pw.stop()
print("FAILURES:", fails if fails else "NONE — synced-rotation checks pass")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Boot + run**

```bash
# boot on :8099 (background, seeded GLB data), then:
<playwright-python> scripts/verify_synced_rotation.py
```

Expected: `FAILURES: NONE — synced-rotation checks pass` (or a clear `SKIP` if the shown pair isn't GLB/GLB — reload to get a mesh pair). Stop the server after.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_synced_rotation.py
git commit -m "test(viewer): Playwright verify for synced A/B camera + feedback gate"
```

---

## Self-Review

**Spec coverage:**

- `syncPair` in `viewer.js` (mesh-only, feedback-safe, no-op on non-mesh) → Task 1 Step 3. ✓
- `arena.js` calls it per pair → Task 1 Step 4. ✓
- Sync orbit + fov + target → `copyCam` (Task 1 Step 3). ✓
- Feedback gate via `detail.source === "user-interaction"` → Task 1 Step 3 + verified in Task 2. ✓
- Mixed/molecular no-op → `if (!a || !b) return` + Task 2 SKIP path. ✓
- Testing (pytest wiring smoke + Playwright behavioral) → Task 1 test + Task 2. ✓

**Placeholder scan:** no TBD/TODO; complete code in every step. Task 2 `<playwright-python>` = the playwright-enabled interpreter used for prior verifications; not a logic placeholder.

**Type/name consistency:** `syncPair(slotA, slotB)` signature identical in viewer.js (Task 1 def + export), arena.js call, the pytest smoke, and the Playwright script; `copyCam(src, dst)` internal; `#slot-a`/`#slot-b` selectors match `arena.html`'s `id="slot-a"`/`id="slot-b"`; `window.Taxon3DViewer.syncPair` matches the export.

**Adjust-on-contact:** confirm the `render()` insertion point (right after the second `mount(...)` that sets `el("fmt-b")`, before `setAB("a")`) against live `arena.js` — line numbers shifted with the onboarding/mobile changes; the anchor (the `fmt-b` mount + the `setAB("a")` call) is stable.
