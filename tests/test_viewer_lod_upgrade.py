"""The LOD upgrade has to fire when a voter zooms. Nothing else in this repo checks that.

This is the coverage whose absence let a broken upgrade ship. `viewer.js` is browser code, the
test suite is pytest, and the gap between them was filled by "it looked right" — so the trigger
fired for roughly 1 voter in 10 in production and nothing failed.

Why it needs a real browser rather than a mocked event: the bug was not in logic that a unit test
could reach. A real wheel WAS delivered to the element (measured: it zoomed the camera and fired
both capture- and bubble-phase probes), and the handler still did not run. Only model-viewer
actually rendering a mesh, owning its own camera, and emitting `camera-change` reproduces it.

Skipped unless all three of its dependencies are present, because each is a genuine external:
Playwright, a real GLB + .lod.glb pair, and network access for the model-viewer CDN — the same
CDN the app itself loads it from. CI has none of them, exactly like the gltf-transform tests.
"""

from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VIEWER_JS = REPO / "app" / "static" / "viewer.js"
MODEL_VIEWER_CDN = "https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js"

#: A bundle carries `x.glb` beside `x.lod.glb`. Point at any directory that has such a pair.
ASSET_DIRS = [
    Path(os.environ.get("BIO3D_LOD_TEST_ASSETS", "")),
    Path("/home/mjarnold/bio3d-arena/public_bundle/v3/assets"),
]

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script type="module" src="%s"></script>
<script src="viewer.js"></script>
<style>html,body{margin:0}#slot{width:320px;height:320px;position:relative}</style>
</head><body>
<div id="slot" class="viewer-slot"></div>
<script>
  window.__mounted = false;
  window.addEventListener('load', () => {
    window.Bio3DViewer.mount(document.getElementById('slot'),
      {url: 'full.glb', lod_url: 'lod.glb', format: 'glb', output_id: 1}, null);
    window.__mounted = true;
  });
</script></body></html>"""


def _find_pair() -> tuple[Path, Path] | None:
    for d in ASSET_DIRS:
        if not d or not d.is_dir():
            continue
        for lod in d.rglob("*.lod.glb"):
            full = lod.with_name(lod.name.replace(".lod.glb", ".glb"))
            if full.is_file():
                return full, lod
    return None


def _playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _playwright() or _find_pair() is None,
    reason="needs Playwright and a real .glb/.lod.glb pair (a built bundle)",
)


@pytest.fixture()
def served(tmp_path):
    """Serve viewer.js and a real mesh pair over HTTP.

    file:// will not do: model-viewer fetches its mesh with XHR, which file:// blocks, and the
    request log this test asserts on would be empty for the wrong reason.
    """
    pair = _find_pair()
    assert pair, "guarded by pytestmark"
    full, lod = pair
    shutil.copy(VIEWER_JS, tmp_path / "viewer.js")
    shutil.copy(full, tmp_path / "full.glb")
    shutil.copy(lod, tmp_path / "lod.glb")
    (tmp_path / "index.html").write_text(PAGE % MODEL_VIEWER_CDN)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

        def log_message(self, format, *args):  # noqa: A002 - signature is BaseHTTPRequestHandler's
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        yield f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
        httpd.shutdown()


def _run(url: str, *, zoom: bool):
    """Load the page, optionally dolly in with a REAL wheel, and report what was fetched."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 420, "height": 420})
        asked: list[str] = []
        page.on("request", lambda r: asked.append(r.url) if ".glb" in r.url else None)
        page.goto(url, wait_until="load", timeout=60_000)
        try:
            page.wait_for_function(
                "() => {const m=document.querySelector('model-viewer'); return m && m.loaded===true;}",
                timeout=60_000,
            )
        except Exception:  # noqa: BLE001
            b.close()
            pytest.skip("model-viewer never loaded (CDN unreachable?)")
        opened_on_lod = any(u.endswith("/lod.glb") for u in asked)
        full_at_open = any(u.endswith("/full.glb") for u in asked)
        if zoom:
            box = page.eval_on_selector(
                "model-viewer",
                "el => {const r = el.getBoundingClientRect(); return {x:r.x+r.width/2, y:r.y+r.height/2};}",
            )
            page.mouse.move(box["x"], box["y"])
            for _ in range(6):  # several notches: one may fall under the zoom threshold
                page.mouse.wheel(0, -240)
                page.wait_for_timeout(150)
            try:
                page.wait_for_function(
                    "() => performance.getEntriesByType('resource').some(e => e.name.endsWith('/full.glb'))",
                    timeout=30_000,
                )
            except Exception:  # noqa: BLE001
                pass
        full_after = any(u.endswith("/full.glb") for u in asked)
        b.close()
    return opened_on_lod, full_at_open, full_after


def test_ballot_opens_on_the_lod(served):
    """The saving itself: the full mesh must NOT be fetched just to show the opening frame."""
    opened_on_lod, full_at_open, _ = _run(served, zoom=False)
    assert opened_on_lod, "the LOD was never requested — the opening frame is not using it"
    assert not full_at_open, (
        "the full mesh was fetched without any interaction — the LOD saves nothing"
    )


def test_zooming_in_fetches_the_full_mesh(served):
    """A voter who zooms must get real geometry. Decimation is invisible at a grid cell and
    obvious up close, and on a fidelity benchmark a voter attributes OUR faceting to the
    generator — so a silent failure here corrupts the measurement, it does not merely slow it.

    HONEST LIMIT, measured rather than assumed: this test **also passes against the pre-fix
    `viewer.js`**. It is a behavioural test, NOT a regression test for the 1-in-10 production
    failure, and it must not be cited as proof that failure is fixed.

    That result is itself the useful finding. The old raw-`wheel` trigger works fine on a page
    that mounts one viewer once, so whatever broke it in production depends on something this
    harness does not reproduce — the ballot lifecycle, i.e. a RE-MOUNT. Which fits the suspected
    mechanism exactly: `{once: true}` could only be consumed to no effect by a wheel arriving
    while a slot was stale, and nothing here ever makes a slot stale.

    The only evidence that carries for the production bug is re-running the live rate harness,
    which measured 1 upgrade in 10 real-input attempts before this change.
    """
    opened_on_lod, full_at_open, full_after = _run(served, zoom=True)
    assert opened_on_lod and not full_at_open, "precondition: must open on the LOD alone"
    assert full_after, (
        "zoomed in and the full mesh was never fetched — the voter is judging a 25%-triangle "
        "mesh and cannot get the real one"
    )
