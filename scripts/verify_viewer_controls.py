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
    fails.append(
        f"Reset did not restore default framing: theta={reset['theta']} phi={reset['phi']}"
    )

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
