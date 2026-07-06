"""Playwright multi-angle capture for VLM-judge contact sheets.

Mirrors render_spotlight._browser_capture_factory but parameterizes the
<model-viewer> camera-orbit azimuth/elevation and returns one PNG per azimuth.
Heavy (browser); built lazily and used only by scripts/judge_vlm.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402


def browser_capture_multi_factory():
    """Returns capture_multi(glb_abs, azimuths, elev) -> list[png bytes]."""
    import http.server
    import socketserver
    import threading

    asset_root = str(config.ASSET_DIR)
    _state: dict = {"glb_rel": "", "az": 30, "elev": 75}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=asset_root, **k)

        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/_render.html"):
                glb_rel, az, elev = _state["glb_rel"], _state["az"], _state["elev"]
                body = (
                    "<!doctype html><html><head>"
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<script type="module" '
                    'src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0'
                    '/model-viewer.min.js"></script>'
                    "</head><body style='margin:0'>"
                    f'<model-viewer id="mv" src="/{glb_rel}" '
                    f'camera-orbit="{az}deg {elev}deg auto" environment-image="neutral" '
                    'shadow-intensity="1" shadow-softness="0.8" '
                    'style="width:512px;height:512px;'
                    'background:linear-gradient(180deg,#b4babf 0%,#6f767c 100%)">'
                    "</model-viewer></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch()

    def capture_multi(glb_abs: str, azimuths: list[int], elev: int) -> list[bytes]:
        # Load the GLB ONCE, then re-aim the camera per azimuth via cameraOrbit +
        # jumpCameraToGoal() (no page reload). The previous new-page-per-azimuth loop re-fetched
        # and re-parsed the mesh for every view — for the large (30-40MB) recon GLBs that meant
        # 8× the load cost and blew the load timeout. One load per output; the wait timeout is
        # generous (120s) so a big mesh finishes parsing. Output is unchanged: one PNG per azimuth.
        _state["glb_rel"] = str(Path(glb_abs).relative_to(config.ASSET_DIR)).replace("\\", "/")
        _state["elev"] = elev
        _state["az"] = azimuths[0] if azimuths else 30
        page = browser.new_page(viewport={"width": 512, "height": 512})
        try:
            page.goto(f"http://127.0.0.1:{port}/_render.html")
            page.wait_for_function("document.querySelector('#mv')?.loaded === true", timeout=120000)
            pngs: list[bytes] = []
            for az in azimuths:
                # Snap (not animate) the camera to this azimuth, then let one frame render.
                page.eval_on_selector(
                    "#mv",
                    "(mv, o) => { mv.cameraOrbit = o; mv.jumpCameraToGoal(); }",
                    f"{az}deg {elev}deg auto",
                )
                page.wait_for_timeout(200)
                pngs.append(page.locator("#mv").screenshot())
        finally:
            page.close()
        return pngs

    return capture_multi
