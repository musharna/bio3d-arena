# Onboarding (P1) — design

> 2026-07-01. Helps a first-time voter understand the arena and discover the (currently invisible)
> keyboard shortcuts, lifting new-voter → first-vote conversion. Pure frontend.

## Goal

A new voter lands on the arena with no guidance. Add (1) a once-shown, non-blocking first-visit
banner explaining the flow + shortcuts, and (2) persistent key-hints on the vote buttons so the
shortcuts (`←`/`→`/`t`/`x`, already wired in `arena.js`) are discoverable. Directly serves the
vote-volume north star.

## Non-goals (YAGNI)

No modal/dialog, no multi-step product tour, no video, no per-page onboarding, no server-side
"seen" tracking. Just the banner + button hints; state lives in `localStorage`.

## Constraints

- Pure frontend: only `app/templates/arena.html`, `app/static/style.css`, `app/static/arena.js`.
- **No flash for returning voters** — the banner is `display:none` by default; JS _shows_ it only
  when `localStorage['bio3d_onboarded']` is unset. (Never shown-by-default-then-hidden.)
- Non-blocking: the banner sits in the page flow above the task card; dismissing it never blocks voting.
- Key-hints are desktop-only (no keyboard on phones) → hide `<kbd>` inside the existing
  `@media (max-width: 720px)` block.
- Desktop layout otherwise unchanged; mobile (P0) untouched.

## Components

### 1. First-visit banner — `arena.html` + `style.css` + `arena.js`

- `arena.html`: at the very top of `<section class="arena">` (before `.filter-row`), add:
  ```html
  <div class="onboard-banner" id="onboard-banner" hidden>
    <span
      >👋 New here? <strong>Rotate & zoom</strong> each model, then vote for the
      better one. Keys: <kbd>←</kbd> A · <kbd>→</kbd> B · <kbd>T</kbd> tie ·
      <kbd>X</kbd> both-bad.</span
    >
    <button
      type="button"
      class="onboard-dismiss"
      id="onboard-dismiss"
      aria-label="Dismiss"
    >
      ✕
    </button>
  </div>
  ```
- `style.css`: `.onboard-banner` styled as a subtle info strip (panel bg, accent left-border,
  rounded, flex with the dismiss button right-aligned). It stays `hidden` via the attribute until
  JS removes it.
- `arena.js`: on init, `if (!localStorage.getItem("bio3d_onboarded")) banner.hidden = false;`. The
  dismiss button sets `localStorage.setItem("bio3d_onboarded", "1")` and `banner.hidden = true`.

### 2. Vote-button key-hints — `arena.html` + `style.css`

- `arena.html`: append a `<kbd>` to each vote button label, e.g.
  `<button class="vote-btn win" data-winner="a">👈 A is better <kbd>←</kbd></button>`,
  Tie→`<kbd>T</kbd>`, Both bad→`<kbd>X</kbd>`, B→`<kbd>→</kbd>`.
- `style.css`: a shared `kbd` style (small, monospace, bordered pill). Inside the existing
  `@media (max-width: 720px)` block, add `.vote-btn kbd { display: none; }` (mobile has no keyboard).

## Data flow

Client-only. On arena load: read `localStorage['bio3d_onboarded']`; show the banner iff unset.
Dismiss writes the flag. No fetch, no server state. Vote flow, keyboard handlers, and the P0 mobile
behavior are unchanged.

## Error handling / edge cases

- **localStorage unavailable** (private mode / disabled): wrap the read/write in try/catch; on
  failure, default to NOT showing the banner (fail quiet — never break the arena over a hint).
- **Returning voter:** flag set → banner never un-hides → no flash.
- **Mobile:** `<kbd>` hints hidden; the banner still shows (its text is useful on mobile too, and
  it references keys harmlessly — acceptable, or the `<kbd>` inside the banner also hides on mobile
  for cleanliness — hide banner `<kbd>` too via the same media rule).

## Testing

- **pytest markup smoke:** `GET /` renders `id="onboard-banner"`, `id="onboard-dismiss"`, and the
  `<kbd>` hints on the vote buttons.
- **Playwright:** with `localStorage` cleared, the banner is visible on load; after clicking dismiss
  it is hidden AND `localStorage['bio3d_onboarded']` is set; reloading keeps it hidden. At 1440px
  the vote-button `<kbd>` hints are visible; at 390px they are hidden.

## Open decisions (defaults chosen)

1. **Banner vs modal** = banner (non-blocking). Default.
2. **Banner placement** = top of `.arena`, above the filters. Default.
3. **localStorage key** = `bio3d_onboarded`. Default.
4. **Banner `<kbd>` on mobile** = hidden (same rule as button `<kbd>`), banner text stays. Default.
