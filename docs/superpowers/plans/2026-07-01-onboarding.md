# Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Help first-time voters — a once-shown, non-blocking first-visit banner explaining the flow + keyboard shortcuts, and persistent `<kbd>` key-hints on the vote buttons.

**Architecture:** Pure frontend. The banner is `hidden` by default and JS reveals it only when `localStorage['bio3d_onboarded']` is unset (no flash for returning voters); dismissing sets the flag. Vote-button `<kbd>` hints are static markup, hidden on mobile via the existing 720px media query.

**Tech Stack:** Jinja2 templates, vanilla CSS + JS. Files: `app/templates/arena.html`, `app/static/style.css`, `app/static/arena.js`. No new deps, no server-side changes.

## Global Constraints

- **No flash for returning voters** — `.onboard-banner` carries the `hidden` attribute in the template; JS only un-hides it when `localStorage['bio3d_onboarded']` is unset. Never shown-by-default.
- **Fail quiet on localStorage errors** — wrap every `localStorage` read/write in try/catch; on read failure, treat as "already onboarded" (do NOT show the banner). Never break the arena over a hint.
- **Key-hints are desktop-only** — `.vote-btn kbd` (and the banner's `<kbd>`) are `display:none` inside the EXISTING `@media (max-width: 720px)` block. No new media query.
- Desktop layout otherwise unchanged; the P0 mobile behavior is untouched.
- `localStorage` key = `bio3d_onboarded`; banner ids = `onboard-banner` / `onboard-dismiss`.

---

### Task 1: Onboarding UI (banner + vote-button key-hints)

**Files:**

- Modify: `app/templates/arena.html` (banner at top of `.arena`; `<kbd>` on each vote button)
- Modify: `app/static/style.css` (`.onboard-banner`, `kbd` styles, mobile `<kbd>` hide)
- Modify: `app/static/arena.js` (show-once + dismiss logic)
- Test: `tests/test_onboarding.py` (create)

**Interfaces:**

- Produces: `#onboard-banner` + `#onboard-dismiss` in the DOM; `<kbd>` hints in the vote buttons; JS that reveals the banner for new visitors and persists dismissal.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_onboarding.py
from fastapi.testclient import TestClient
from app.main import app


def test_onboarding_markup_present():
    html = TestClient(app).get("/").text
    assert 'id="onboard-banner"' in html
    assert 'id="onboard-dismiss"' in html
    # the banner is hidden by default (revealed by JS only for new visitors)
    assert 'class="onboard-banner"' in html and " hidden" in html
    # key hints on vote buttons
    assert "<kbd>" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_onboarding.py -v`
Expected: FAIL — `assert 'id="onboard-banner"' in html`.

- [ ] **Step 3: Edit `arena.html`**

Immediately after `<section class="arena">` (line 3), BEFORE `<div class="filter-row">`, insert:

```html
<div class="onboard-banner" id="onboard-banner" hidden>
  <span
    >👋 New here? <strong>Rotate &amp; zoom</strong> each model, then vote for
    the better one. Keys: <kbd>←</kbd> A · <kbd>→</kbd> B · <kbd>T</kbd> tie ·
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

Then append a `<kbd>` to each of the 4 vote buttons (lines 63-66) so they read:

```html
<button class="vote-btn win" data-winner="a">
  👈 A is better <kbd>←</kbd>
</button>
<button class="vote-btn tie" data-winner="tie">🤝 Tie <kbd>T</kbd></button>
<button class="vote-btn bad" data-winner="bad">👎 Both bad <kbd>X</kbd></button>
<button class="vote-btn win" data-winner="b">
  B is better 👉 <kbd>→</kbd>
</button>
```

- [ ] **Step 4: Edit `style.css`**

Add near the other component styles (e.g. after the `.vote-btn` rules, ~line 410):

```css
.onboard-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  background: var(--panel);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 10px;
  padding: 0.7rem 1rem;
  margin-bottom: 1rem;
  color: var(--muted);
  font-size: 0.95rem;
}
.onboard-banner strong {
  color: var(--text);
}
.onboard-dismiss {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 1.1rem;
  cursor: pointer;
  flex-shrink: 0;
}
.onboard-dismiss:hover {
  color: var(--text);
}
kbd {
  font-family: ui-monospace, monospace;
  font-size: 0.8em;
  padding: 0.1em 0.4em;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--panel2);
  color: var(--text);
}
```

Then INSIDE the existing `@media (max-width: 720px)` block (line ~591), add:

```css
.vote-btn kbd,
.onboard-banner kbd {
  display: none;
}
```

- [ ] **Step 5: Edit `arena.js`**

At the top level of `app/static/arena.js` (e.g. after the `el` helper on line ~6), add:

```js
// First-visit onboarding banner: shown once, state persisted in localStorage. Fail-quiet.
(function initOnboarding() {
  const banner = document.getElementById("onboard-banner");
  const dismiss = document.getElementById("onboard-dismiss");
  if (!banner || !dismiss) return;
  let seen = true;
  try {
    seen = !!localStorage.getItem("bio3d_onboarded");
  } catch (e) {
    seen = true; // localStorage unavailable → don't show, never break the arena
  }
  if (!seen) banner.hidden = false;
  dismiss.addEventListener("click", () => {
    banner.hidden = true;
    try {
      localStorage.setItem("bio3d_onboarded", "1");
    } catch (e) {
      /* ignore */
    }
  });
})();
```

(This runs at parse time like the existing `.vote-btn` handler on line ~145, which already assumes the DOM is present — arena.js loads after the body.)

- [ ] **Step 6: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_onboarding.py -v` → PASS.
Run: `.venv/bin/pytest -q` → no regressions.

- [ ] **Step 7: Commit**

```bash
git add app/templates/arena.html app/static/style.css app/static/arena.js tests/test_onboarding.py
git commit -m "feat(onboarding): first-visit banner + vote-button key hints"
```

---

### Task 2: Playwright verification

**Files:**

- Create: `scripts/verify_onboarding.py`

**Interfaces:**

- Consumes: a running instance on :8099. Asserts banner show-once + dismiss persistence + responsive `<kbd>`.

- [ ] **Step 1: Write the verification script**

```python
# scripts/verify_onboarding.py
"""Verify onboarding: banner shows for a new visitor, hides + persists on dismiss, stays hidden on
reload; vote-button <kbd> hints visible at 1440px, hidden at 390px. Boot the app on :8099 first."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
pw = sync_playwright().start()
b = pw.chromium.launch(args=["--no-sandbox"])
ctx = b.new_context(viewport={"width": 1440, "height": 1000})
fails = []

p = ctx.new_page()
p.goto(BASE + "/", wait_until="networkidle", timeout=15000)
p.evaluate("localStorage.removeItem('bio3d_onboarded')")
p.reload(wait_until="networkidle")
p.wait_for_timeout(500)
if not p.is_visible("#onboard-banner"):
    fails.append("banner not visible for new visitor")
if not p.is_visible(".vote-btn kbd"):
    fails.append("vote-button kbd hints not visible at 1440px")
p.click("#onboard-dismiss")
p.wait_for_timeout(200)
if p.is_visible("#onboard-banner"):
    fails.append("banner still visible after dismiss")
flag = p.evaluate("localStorage.getItem('bio3d_onboarded')")
if flag != "1":
    fails.append(f"localStorage flag not set (got {flag!r})")
p.reload(wait_until="networkidle")
p.wait_for_timeout(400)
if p.is_visible("#onboard-banner"):
    fails.append("banner reappeared after reload (should stay dismissed)")

# mobile: kbd hidden
m = b.new_context(viewport={"width": 390, "height": 844}).new_page()
m.goto(BASE + "/", wait_until="networkidle", timeout=15000)
m.wait_for_timeout(400)
if m.is_visible(".vote-btn kbd"):
    fails.append("vote-button kbd hints visible at 390px (should be hidden)")

b.close()
pw.stop()
print("FAILURES:", fails if fails else "NONE — onboarding checks pass")
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Boot + run**

```bash
# boot on :8099 (background, seeded DB), then run with the playwright interpreter:
<playwright-python> scripts/verify_onboarding.py
```

Expected: `FAILURES: NONE — onboarding checks pass`. Stop the server after.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_onboarding.py
git commit -m "test(onboarding): Playwright verify (show-once, dismiss-persist, responsive kbd)"
```

---

## Self-Review

**Spec coverage:**

- First-visit banner (hidden-by-default, JS reveals for new visitors, dismiss persists) → Task 1 (arena.html + arena.js). ✓
- Vote-button `<kbd>` key-hints → Task 1 (arena.html + style.css). ✓
- Desktop-only kbd (hidden ≤720px) → Task 1 Step 4 media rule. ✓
- Fail-quiet localStorage → Task 1 Step 5 try/catch. ✓
- No-flash for returning voters → `hidden` attr default + JS-reveal-only. ✓
- Testing (pytest markup + Playwright show-once/dismiss/responsive) → Task 1 test + Task 2. ✓

**Placeholder scan:** no TBD/TODO; complete code in every step. Task 2 Step 2 `<playwright-python>` = the interpreter with playwright (the one used for prior mobile verification); not a logic placeholder.

**Type/selector consistency:** `#onboard-banner` / `#onboard-dismiss` / `bio3d_onboarded` / `.onboard-banner` / `.vote-btn kbd` identical across arena.html (Task 1 markup), style.css (Task 1), arena.js (Task 1 JS), and verify script (Task 2). The `hidden` attribute default + JS `banner.hidden = false` reveal are consistent.

**Adjust-on-contact:** confirm the `<section class="arena">` opening line and the 4 `.vote-btn` lines against live `arena.html` (line numbers may shift; the anchors — the `arena` section tag and the `data-winner` buttons — are stable).
