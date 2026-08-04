# Synced A/B rotation (P1) — design

> 2026-07-01. The signature comparison feature: rotate/zoom/pan one arena viewer and the other
> mirrors the exact camera, so morphology is compared at the identical angle. Complements the
> mobile A/B toggle. Pure frontend. GLB/GLTF mesh pairs only.

## Goal

When both models in a pair are meshes (`<model-viewer>`), keep their cameras in lock-step: a
user drag / zoom / pan on either viewer applies the same orbit + field-of-view + target to the
other. This makes side-by-side (and mobile A/B-toggle) comparison meaningful.

## Non-goals (YAGNI)

- No sync for molecular (3Dmol) viewers or mixed mesh/molecular pairs — different modalities;
  comparing them "at the same angle" is meaningless. They rotate independently.
- No user-facing "unlink / independent rotation" toggle (deferrable; always-on for mesh pairs).
- No sync of lighting/exposure, no shared-camera persistence across pairs.

## Constraints

- Pure frontend: only `app/static/viewer.js` and `app/static/arena.js`. No server change, no deps.
- **Feedback-loop-safe:** propagate ONLY user-initiated camera changes. model-viewer's
  `camera-change` event carries `detail.source`; sync only when `detail.source === 'user-interaction'`.
  Programmatic writes fire `source: 'none'`, which is ignored — so applying A→B never bounces back.
- **Fresh per pair:** `mount()` tears down + recreates the `<model-viewer>` for each new pair, so
  sync is re-wired on new elements each render — no stale listeners accumulate.
- Degrade gracefully: if either slot lacks a `<model-viewer>` (molecular, mixed, or a load
  failure), `syncPair` is a no-op — never throws.

## Verified model-viewer 3.5.0 camera API

- Read: `mv.getCameraOrbit()` → Spherical with `.toString()` → a valid `camera-orbit` string;
  `mv.getCameraTarget()` → `.toString()` → a `camera-target` string; `mv.getFieldOfView()` → number (deg).
- Write: set `mv.cameraOrbit` / `mv.cameraTarget` (strings), `mv.fieldOfView = n + "deg"`, then
  `mv.jumpCameraToGoal()` to apply immediately.
- Event: `camera-change` with `event.detail.source` (`"user-interaction"` | `"none"`).

## Components

### 1. `viewer.js` — `syncPair(slotA, slotB)`

- Find `const a = slotA.querySelector("model-viewer"); const b = slotB.querySelector("model-viewer");`
  If either is null → `return` (no-op; not a mesh/mesh pair).
- Define `copyCam(src, dst)`:
  ```js
  dst.cameraOrbit = src.getCameraOrbit().toString();
  dst.cameraTarget = src.getCameraTarget().toString();
  dst.fieldOfView = src.getFieldOfView() + "deg";
  dst.jumpCameraToGoal();
  ```
- Wire both directions, gated on user-interaction:
  ```js
  a.addEventListener("camera-change", (e) => {
    if (e.detail.source === "user-interaction") copyCam(a, b);
  });
  b.addEventListener("camera-change", (e) => {
    if (e.detail.source === "user-interaction") copyCam(b, a);
  });
  ```
- Expose on `window.Taxon3DViewer.syncPair`. (`mount()` is unchanged.)

### 2. `arena.js` — call it after mounting a pair

- In `render(data)`, after the two `Taxon3DViewer.mount(...)` calls, add
  `window.Taxon3DViewer.syncPair(el("slot-a"), el("slot-b"));`.

## Data flow

Client-only. User drags viewer A → model-viewer fires `camera-change` (`source: "user-interaction"`)
→ `copyCam(a, b)` reads A's camera and writes B's + `jumpCameraToGoal()` → B fires `camera-change`
(`source: "none"`) → ignored (gate) → no bounce. Symmetric for B→A. No fetch, no server state.

## Error handling / edge cases

- **Molecular or mixed pair:** one/both slots have a 3Dmol canvas, not a `<model-viewer>` →
  `querySelector("model-viewer")` is null → `syncPair` no-ops → independent rotation (correct).
- **Load timing:** `camera-change` only fires after the model loads + the user interacts; by then
  `getCameraOrbit()` is valid. Wiring listeners immediately is safe (they simply never fire until load).
- **Rapid voting / re-mount:** each `render()` recreates the viewers and re-calls `syncPair`; the old
  elements (with their listeners) are discarded by `mount()`'s teardown — no leak, no cross-pair sync.
- **`jumpCameraToGoal` re-entrancy:** it fires a `camera-change` with `source: "none"` → gated out.

## Testing

- **Existing pytest suite stays green** (no server change).
- **Playwright (GLB/GLB pair):** after both viewers load, set A's camera via the API
  (`slotA model-viewer.cameraOrbit = "1rad 1.2rad 3m"; .jumpCameraToGoal()`) and dispatch a
  `camera-change` `CustomEvent` with `detail:{source:"user-interaction"}` on A; assert B's
  `getCameraOrbit().toString()` now matches A's (within rounding). Then dispatch a programmatic-source
  event and assert B does NOT change again (feedback gate). **Mixed pair:** confirm `syncPair` no-ops
  when one slot is molecular (no `<model-viewer>`), i.e. it doesn't throw and both rotate freely.

## Open decisions (defaults chosen)

1. **Scope** = mesh/mesh only (GLB/GLTF). Molecular/mixed = independent. Default.
2. **Always-on** (no unlink toggle) for v1. Default.
3. **Sync orbit + fov + target** (full view), not just orbit — so "the same view", including zoom + pan.
