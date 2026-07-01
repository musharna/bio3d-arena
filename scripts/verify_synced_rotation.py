"""Verify synced A/B rotation on a GLB/GLB pair: a user-source camera-change on A mirrors A's
camera onto B; a programmatic-source event does NOT (feedback gate). Boot the app on :8099 first,
run with a playwright interpreter. Exit 0 on pass (or a graceful SKIP if the shown pair isn't mesh)."""

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
    print("SKIP: current pair is not GLB/GLB (nothing to sync) — reload to get a mesh pair")
    b.close()
    pw.stop()
    sys.exit(0)
if ready != "ready":
    fails.append(f"model-viewers not loaded: {ready}")

result = p.evaluate(
    """() => {
      const a = document.querySelector('#slot-a model-viewer');
      const b = document.querySelector('#slot-b model-viewer');
      a.cameraOrbit = '0.7rad 1.1rad 2.5m'; a.jumpCameraToGoal();
      a.dispatchEvent(new CustomEvent('camera-change', {detail:{source:'user-interaction'}}));
      const oa = a.getCameraOrbit(), ob = b.getCameraOrbit();
      // Rotation (theta, phi) must sync exactly. Radius (dolly distance) is clamped per-model to
      // each model's auto-framed bounds, so it can differ slightly for differently-sized models
      // — assert it's close, not identical.
      return {
        thetaMatch: Math.abs(oa.theta - ob.theta) < 0.02,
        phiMatch: Math.abs(oa.phi - ob.phi) < 0.02,
        radiusClose: oa.radius > 0 && Math.abs(oa.radius - ob.radius) / oa.radius < 0.2,
        a: [oa.theta, oa.phi, oa.radius], b: [ob.theta, ob.phi, ob.radius],
      };
    }"""
)
if not result["thetaMatch"] or not result["phiMatch"]:
    fails.append(f"rotation not synced: A={result['a']} B={result['b']}")
if not result["radiusClose"]:
    fails.append(f"zoom (radius) not close: A={result['a']} B={result['b']}")

gate = p.evaluate(
    """() => {
      const a = document.querySelector('#slot-a model-viewer');
      const b = document.querySelector('#slot-b model-viewer');
      const before = b.getCameraOrbit().toString();
      a.cameraOrbit = '1.4rad 0.9rad 3m'; a.jumpCameraToGoal();
      a.dispatchEvent(new CustomEvent('camera-change', {detail:{source:'none'}}));
      return before === b.getCameraOrbit().toString();
    }"""
)
if not gate:
    fails.append("feedback gate failed: B followed a programmatic-source camera-change")

b.close()
pw.stop()
print("FAILURES:", fails if fails else "NONE — synced-rotation checks pass")
sys.exit(1 if fails else 0)
