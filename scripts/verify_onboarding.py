"""Verify onboarding: banner shows for a new visitor, hides + persists on dismiss, stays hidden on
reload; vote-button <kbd> hints visible at 1440px, hidden at 390px. Boot the app on :8099 first,
then run with a playwright interpreter. Exit 0 on all-pass, 1 otherwise."""

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
