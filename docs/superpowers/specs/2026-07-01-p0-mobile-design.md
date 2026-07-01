# P0 Mobile — design

> 2026-07-01. Fixes the launch-critical mobile UX gaps found in the visual audit: the nav wraps
> into a broken 6-line jumble on phones, the A/B viewers stack so you can't compare them, and the
> vote buttons sit far below the fold. Pure frontend (templates + CSS + JS); desktop unchanged.

## Goal

Make the arena usable — and votable — on a phone. Mobile voting UX is the lever for the vote
volume that is the arena's one remaining competitive gap, so a public launch needs this.

## Non-goals (YAGNI — these are the separate P1 slice)

Synced A/B rotation, onboarding/first-vote hints, extra viewer controls (reset/fullscreen), nav
de-cluttering on desktop, share/embed. Not now.

## Constraints

- **Desktop is untouched above the breakpoint** — all changes live inside `@media (max-width: 720px)`
  (nav) / the mobile branch of the arena layout. Desktop screenshots must be pixel-identical.
- **No new dependencies, no server-side changes** — only `app/templates/base.html`,
  `app/templates/arena.html`, `app/static/style.css`, `app/static/arena.js`.
- **Nav must work without JS** (it is global chrome) → CSS-only hamburger. The arena is already
  JS-dependent (it fetches pairs), so the A/B toggle may use a few lines of JS.
- Breakpoint: **720px** — the codebase ALREADY has a `@media (max-width:720px)` block that sets `.pair`→1-col and `.vote-bar`→2-col; EXTEND that existing block rather than adding a new one. The nav-collapse is the missing piece.

## Components

### 1. Responsive nav (CSS-only hamburger) — `base.html` + `style.css`

- In `base.html`, wrap the nav toggle as a **checkbox hack**: a visually-hidden
  `<input type="checkbox" id="nav-toggle">` + a `<label for="nav-toggle" class="nav-burger">☰</label>`
  placed in the `.topbar`. The existing `<nav>` stays as-is.
- CSS: the `.nav-burger` is `display:none` on desktop. In `@media (max-width:720px)`: show the
  burger, `.topbar > nav` becomes `display:none` by default and `#nav-toggle:checked ~ nav`
  (or a wrapper) switches it to a stacked vertical drop-down (full-width links, larger tap
  targets ≥44px). Desktop keeps the horizontal bar unchanged.
- The login link / verified-user chip live inside `<nav>` and inherit the collapse.

### 2. Mobile A/B toggle — `arena.html` + `style.css` + `arena.js`

- In `arena.html`, add a segmented control just above `.pair`:
  `<div class="ab-toggle" role="tablist" aria-label="Which model to view">`
  with two buttons `data-ab="a"` / `data-ab="b"` (labels "Model A" / "Model B"). Hidden on desktop.
- CSS: desktop `.pair` stays a 2-col grid (both `.model-col` visible); `.ab-toggle` is
  `display:none`. In `@media (max-width:720px)`: `.pair` becomes one column; `.ab-toggle` shows;
  a `.model-col` is `display:none` unless it has an `is-active` class. Default: `.model-col:first-child`
  (A) active. Hiding a `.model-col` with `display:none` also **pauses its `<model-viewer>`** (a perf
  win on mobile).
- `arena.js`: on `.ab-toggle` button click, toggle `is-active` between the two `.model-col`s and set
  `aria-selected`/`aria-pressed`. Guard: the toggle only affects layout when the mobile CSS is
  active; on desktop both cols show regardless (the `is-active` class is inert because desktop CSS
  shows both). The toggle must re-assert A-active whenever a new pair loads (hook the existing
  pair-render path in `arena.js`).

### 3. Sticky vote bar — `style.css`

- In `@media (max-width:720px)`: `.vote-bar` becomes `position: sticky; bottom: 0` with a solid/
  blurred background + top border + small padding, so the 4 vote buttons are always reachable
  without scrolling past a ~60vh viewer. Desktop unchanged (static below the pair).
- Ensure the sticky bar doesn't cover the `.status-line`; add bottom padding to `.arena` on mobile
  equal to the bar height.

## Data flow

No server data flow changes. Client only: checkbox toggles nav visibility (CSS); `.ab-toggle`
click toggles `.model-col.is-active` (JS); CSS sticky positions the vote bar. The vote flow,
keyboard shortcuts, and pair-loading in `arena.js` are unchanged except for the one hook that
re-asserts "A active" on each new pair.

## Error handling / edge cases

- **JS disabled:** nav still works (CSS-only). Arena already needs JS to load pairs, so the A/B
  toggle degrading is moot (no pairs without JS anyway); as a safety net, if `.ab-toggle` never
  runs, both `.model-col`s should remain visible (default state = A active but B reachable) rather
  than hiding B — i.e., the mobile CSS hides B only when JS has set `is-active` on A. (Achieve by
  gating the "hide inactive" rule on a JS-added class like `body.js-ab` so no-JS keeps both shown.)
- **Model-viewer in a hidden col:** confirmed model-viewer pauses when `display:none`; switching
  back resumes. No leak.
- **Rotation between breakpoints:** rotating a phone across 720px re-applies the correct layout via
  CSS; the `is-active` class is harmless on desktop.

## Testing

Frontend-only, so verification is visual + regression:

- **Existing pytest suite stays green** (no server change; `/` and pages still render 200 — a
  smoke assertion that `.ab-toggle` and `nav-burger` markup is present is enough on the Python side).
- **Playwright visual checks** (the audit harness) at **390px**: (a) the `.nav-burger` is visible and
  `<nav>` is collapsed until the checkbox is toggled; (b) the `.ab-toggle` is visible and clicking
  "Model B" hides A's viewer and shows B's; (c) `.vote-bar` is sticky (stays in viewport on scroll).
  At **1440px**: the burger + toggle are hidden and the layout matches the pre-change desktop
  screenshot (no regression).

## Open decisions (defaults chosen)

1. **Breakpoint** = 720px (covers phones; tablets keep desktop layout). Default.
2. **A/B toggle style** = segmented buttons (not a swipe gesture — simpler, accessible, discoverable).
3. **No-JS safety** = gate the "hide inactive model-col" rule on a `body.js-ab` class so no-JS shows
   both models stacked (graceful degradation).
