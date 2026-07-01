# Viewer controls: Reset + Fullscreen (P1) — design

> 2026-07-01. A small per-slot overlay toolbar on every 3D viewer with two buttons:
> **⟳ Reset view** and **⛶ Fullscreen**. Lets a voter re-frame a model they've spun off-axis and
> inspect a single model full-screen. Pure frontend. Works for both mesh (`<model-viewer>`) and
> molecular (3Dmol) viewers. Complements the mobile A/B toggle, onboarding, and synced rotation.

## Goal

Each `.viewer-slot` shows a small toolbar (top-right) with **Reset view** and **Fullscreen**.
Reset restores the viewer's default framing; Fullscreen makes that single slot fill the screen for
close inspection (exit returns to the pair). Applies to both viewer modalities.

## Non-goals (YAGNI)

- No download / screenshot / AR / share buttons — two buttons only.
- No "fullscreen both models side-by-side" — fullscreen is **per-slot** (one model fills the screen).
- No persistence of camera or fullscreen state across pairs or reloads.
- No keyboard shortcut for reset/fullscreen (mouse/tap only; the vote keys are unchanged).
- No sync of a reset across the A/B pair — reset is per-slot and does not propagate (see below).

## Constraints

- **Pure frontend:** only `app/static/viewer.js` and `app/static/style.css`. No server change, no
  `arena.js` change (mount is already invoked there), no new dependencies.
- **Both modalities:** mesh (`<model-viewer>`) and molecular (3Dmol) viewers both get the toolbar.
- **Only on a successful mount:** the failed / unsupported-format paths render no toolbar.
- **Fresh per pair:** `mount()` does `slot.innerHTML = ""`, which removes the old toolbar and its
  buttons; each new mount re-adds the toolbar and reassigns the per-slot closures. The single
  `fullscreenchange` listener is module-level (added once at IIFE init) — never per-mount — so no
  listener leak across rapid re-mounts.
- **Reset does not propagate across the synced pair:** the mesh reset sets the camera
  programmatically, which fires `camera-change` with `detail.source === "none"`; `syncPair` only
  propagates `"user-interaction"` events, so resetting A leaves B untouched (intended).

## Verified viewer APIs

- **model-viewer 3.5.0 (mesh):** default `camera-orbit` is `"0deg 75deg auto"`, default
  `field-of-view` is `"auto"` (auto-frames to fit), default `camera-target` is `"auto auto auto"`.
  Writing `cameraOrbit` / `fieldOfView` / `cameraTarget` then calling `jumpCameraToGoal()` applies
  immediately. Setting these before the model finishes loading is honored (no error).
- **3Dmol (molecular):** `viewer.zoomTo()` re-centers/zooms to fit; `viewer.resize()` re-reads the
  container size (needed after the slot changes size on fullscreen enter/exit); `viewer.render()`
  repaints. The viewer instance is currently a local in `mountMolecular` — it will be stored on the
  slot so the toolbar can reach it.
- **Fullscreen API:** `Element.requestFullscreen()` / `document.exitFullscreen()` /
  `document.fullscreenElement`. A `click` from Playwright or a real user is a trusted gesture, so
  `requestFullscreen()` is permitted (including headless Chromium).

## Components (all in `viewer.js`)

### 1. `addControls(slot)`

- Builds `<div class="viewer-controls">` containing two `<button type="button">`:
  - Reset: `class="viewer-ctl"`, `aria-label="Reset view"`, `title="Reset view"`, text/glyph `⟳`.
  - Fullscreen: `class="viewer-ctl"`, `aria-label="Fullscreen"`, `title="Fullscreen"`, glyph `⛶`.
- Reset button `click` → `if (slot._resetView) slot._resetView();`
- Fullscreen button `click` → `toggleFullscreen(slot);`
- Appends the toolbar to `slot`.
- Called **only** at the end of a successful mesh mount and a successful molecular mount (never on
  the failed/unsupported paths).

### 2. Per-slot closures set by the mounts

- **`mountMesh`** (synchronously, after creating `mv`, before `slot.appendChild(mv)` returns):
  ```js
  slot._resetView = () => {
    mv.cameraOrbit = "0deg 75deg auto";
    mv.fieldOfView = "auto";
    mv.cameraTarget = "auto auto auto";
    mv.jumpCameraToGoal();
  };
  ```
  Mesh needs no `_onResize` (model-viewer auto-resizes to its container). Then `addControls(slot)`.
- **`mountMolecular`** (inside the `try`, after `viewer.render()` and the stale-gen check that
  already guards that block):
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
  ```
  Then `addControls(slot)`.

### 3. `toggleFullscreen(slot)`

```js
function toggleFullscreen(slot) {
  if (document.fullscreenElement) document.exitFullscreen();
  else if (slot.requestFullscreen) slot.requestFullscreen();
}
```

### 4. Module-level `fullscreenchange` handler (added once in the IIFE)

Tracks the slot currently/previously fullscreen so molecular viewers resize on both enter and exit;
mesh `_onResize` is absent (no-op). Wrapped in try/catch so a torn-down slot can't throw:

```js
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

## Styling (`style.css`)

- `.viewer-controls`: `position: absolute; top: 8px; right: 8px;` `display: flex; gap: 6px;`
  `z-index: 4` (above `.viewer-hint`'s `z-index: 3`). `pointer-events: auto`.
- `.viewer-ctl`: small square button (~30px), rounded, `background: rgba(15,20,25,0.72)`, light
  glyph color, `border: 1px solid var(--border)`, `cursor: pointer`. Default `opacity: 0.55`,
  transition on opacity.
- Desktop hover reveal + focus: `.viewer-slot:hover .viewer-controls, .viewer-ctl:focus { opacity: 1; }`
  (the container fades in on slot hover; individual button opacity is 1 on hover/focus).
- **Touch (no hover):** inside the existing `@media (hover: none)` — or the `max-width: 720px`
  block if simpler — keep `.viewer-controls { opacity: 1; }` so the buttons are always tappable on
  mobile. (Implementation picks `@media (hover: none)` — it targets touch precisely without tying to
  a width breakpoint.)
- No change to `.viewer-slot` sizing: it is already `position: relative`, so the absolute toolbar
  anchors to it; in fullscreen the slot fills the screen and the toolbar stays pinned top-right.

## Data flow

Client-only, no fetch/server state. Reset: user clicks ⟳ → `slot._resetView()` writes the default
camera (mesh) or `zoomTo` (molecular) → immediate repaint. Fullscreen: user clicks ⛶ →
`slot.requestFullscreen()` → browser fires `fullscreenchange` → module handler calls the slot's
`_onResize` (molecular resize; mesh no-op) → click again → `exitFullscreen()` → `fullscreenchange`
→ resize back.

## Error handling / edge cases

- **Failed / unsupported format:** `failed(...)` renders an error box and `addControls` is not
  called → no toolbar (correct — nothing to reset/fullscreen).
- **Rapid re-mount:** `mount()`'s `slot.innerHTML = ""` removes the toolbar DOM; the new mount
  re-adds it and reassigns `_resetView` / `_onResize`. The one `fullscreenchange` listener is
  module-level, so it is not re-added. If a slot is torn down while fullscreen, the browser exits
  fullscreen (firing `fullscreenchange`); the handler's try/catch swallows any resize error on the
  now-dead 3Dmol viewer.
- **Reset before mesh load:** setting `cameraOrbit` etc. before the model loads is honored by
  model-viewer; `jumpCameraToGoal()` is safe. (The buttons appear immediately on mount, before the
  spinner clears — acceptable; reset simply re-asserts the default.)
- **Browser without Fullscreen API:** `slot.requestFullscreen` is guarded (`if (slot.requestFullscreen)`);
  absent → the button no-ops rather than throwing.

## Testing

- **Existing pytest suite stays green** (no server change).
- **pytest markup smoke** (`tests/test_viewer_controls.py`): `GET /static/viewer.js` contains
  `viewer-controls`, `requestFullscreen`, `Reset view`, and `Fullscreen` (aria-labels), and
  `_resetView`.
- **Playwright behavioral** (`scripts/verify_viewer_controls.py`, on a GLB/GLB pair):
  1. Wait for both `<model-viewer>`s to load. If not a mesh pair → `SKIP` (graceful, exit 0).
  2. Drag A off-default: set `a.cameraOrbit = "1.4rad 0.9rad 3m"; a.jumpCameraToGoal()`; confirm it
     moved. Click A's Reset button; assert A's `getCameraOrbit()` theta ≈ 0 and phi ≈ 75° (default),
     i.e. it returned to the default framing (tolerance for rounding).
  3. Click A's Fullscreen button; assert `document.fullscreenElement` is A's `.viewer-slot`. Click
     again; assert `document.fullscreenElement === null`.
  4. Confirm the toolbar exists in each `.viewer-slot` (two `.viewer-ctl` buttons).

## Open decisions (defaults chosen)

1. **Both modalities** get the toolbar (mesh + molecular). Chosen.
2. **Per-slot fullscreen** (one model fills the screen), not side-by-side. Chosen.
3. **Reset is per-slot**, does not propagate across the synced pair (the `source:"none"` gate makes
   this automatic). Chosen.
4. Glyphs: `⟳` (reset) and `⛶` (fullscreen), with text `aria-label`/`title` for a11y. Chosen.
