"""Visual verification for the P0 mobile changes. Boot the app on :8099 first, then run
(with a playwright-enabled interpreter). Asserts, at 390px: nav collapsed to a burger that
expands on click; the A/B toggle switches the visible viewer; the vote bar is sticky. At 1440px:
the burger + A/B toggle are hidden (desktop unchanged). Exit 0 on all-pass, 1 otherwise.
"""

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
pw = sync_playwright().start()
b = pw.chromium.launch(args=["--no-sandbox"])
fails = []

# --- mobile (390px) ---
m = b.new_page(viewport={"width": 390, "height": 844})
m.goto(BASE + "/", wait_until="networkidle", timeout=15000)
m.wait_for_timeout(1500)
if not m.is_visible(".nav-burger"):
    fails.append("nav-burger not visible at 390px")
if m.is_visible("header.topbar nav a[href='/leaderboard']"):
    fails.append("nav links visible before toggle (should be collapsed)")
m.click(".nav-burger")
m.wait_for_timeout(200)
if not m.is_visible("header.topbar nav a[href='/leaderboard']"):
    fails.append("nav did not expand after burger click")
m.click(".nav-burger")  # collapse again so it doesn't cover the toggle
if m.is_visible(".ab-toggle"):
    m.click(".ab-btn[data-ab='b']")
    m.wait_for_timeout(300)
    a_vis = m.is_visible(".pair .model-col:nth-child(1) .viewer-slot")
    b_vis = m.is_visible(".pair .model-col:nth-child(2) .viewer-slot")
    if a_vis or not b_vis:
        fails.append(f"A/B toggle wrong: A_visible={a_vis} B_visible={b_vis}")
    m.click(".ab-btn[data-ab='a']")
    m.wait_for_timeout(200)
    if not m.is_visible(".pair .model-col:nth-child(1) .viewer-slot"):
        fails.append("switching back to A did not show A")
else:
    fails.append(".ab-toggle not visible at 390px")
vb = m.query_selector(".vote-bar")
if vb and vb.evaluate("el => getComputedStyle(el).position") != "sticky":
    fails.append("vote-bar not position:sticky at 390px")

# --- desktop regression (1440px) ---
d = b.new_page(viewport={"width": 1440, "height": 1000})
d.goto(BASE + "/", wait_until="networkidle", timeout=15000)
d.wait_for_timeout(500)
if d.is_visible(".nav-burger"):
    fails.append("nav-burger visible at 1440px (should be hidden)")
if d.is_visible(".ab-toggle"):
    fails.append("ab-toggle visible at 1440px (should be hidden)")
# both model columns visible side-by-side on desktop
if not (
    d.is_visible(".pair .model-col:nth-child(1)") and d.is_visible(".pair .model-col:nth-child(2)")
):
    fails.append("desktop: both model columns not visible side-by-side")

b.close()
pw.stop()
print("FAILURES:", fails if fails else "NONE — all mobile + desktop checks pass")
sys.exit(1 if fails else 0)
