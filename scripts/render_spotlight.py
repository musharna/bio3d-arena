"""Capture a static <model-viewer> PNG thumbnail per ModelOutput → Critique.render_path.

A transient http.server roots ASSET_DIR so model-viewer fetches the GLB over http
(file:// is blocked by browser security). Playwright drives model-viewer, waits for
its load event, screenshots the element. Commits per output (never holds the SQLite
write lock across a render — see recon_service.rescore_all)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.models import Critique, ModelOutput  # noqa: E402


def _get_or_create_critique(db, output_id: int) -> Critique:
    c = db.execute(select(Critique).where(Critique.output_id == output_id)).scalars().first()
    if c is None:
        c = Critique(output_id=output_id)
        db.add(c)
        db.flush()
    return c


def render_outputs(db, output_ids: list[int], *, capture) -> dict:
    """capture(glb_abs_path: str) -> png bytes. Injectable for tests."""
    rendered = errors = 0
    renders_dir = Path(config.ASSET_DIR) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    for oid in output_ids:
        out = db.get(ModelOutput, oid)
        c = _get_or_create_critique(db, oid)
        if out is None:
            c.status = "error"
            c.critic_note = f"output {oid} not found"
            errors += 1
            db.commit()
            continue
        try:
            glb_abs = str(Path(config.ASSET_DIR) / out.asset_path)
            png = capture(glb_abs)
            rel = f"renders/{oid}.png"
            (Path(config.ASSET_DIR) / rel).write_bytes(png)
            c.render_path = rel
            c.status = "ok"
            rendered += 1
        except Exception as e:  # noqa: BLE001 — best-effort batch
            c.status = "error"
            c.critic_note = f"render failed: {e}"
            errors += 1
        db.commit()  # per-output: release the write lock between renders
    return {"rendered": rendered, "errors": errors}


def _browser_capture_factory():
    """Real capture: a transient static server + Playwright model-viewer screenshot.
    Returns capture(glb_abs_path) -> png bytes. Heavy import, so built lazily.

    The HTML page is served from the same local server as the GLB so the page
    origin is http://127.0.0.1:<port> (not null), which satisfies the CORS policy
    that model-viewer enforces when fetching the GLB via XHR/fetch.
    """
    import http.server
    import socketserver
    import threading

    asset_root = str(config.ASSET_DIR)

    # Current GLB rel-path for the active render; mutated per capture() call.
    _state: dict = {"glb_rel": ""}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=asset_root, **k)

        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            if self.path == "/_render.html":
                glb_rel = _state["glb_rel"]
                body = (
                    "<!doctype html><html><head>"
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<script type="module" '
                    'src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0'
                    '/model-viewer.min.js"></script>'
                    "</head><body style='margin:0'>"
                    f'<model-viewer id="mv" src="/{glb_rel}" '
                    'camera-orbit="30deg 75deg auto" environment-image="neutral" '
                    'style="width:512px;height:512px;background:#fff">'
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

    def capture(glb_abs_path: str) -> bytes:
        _state["glb_rel"] = str(Path(glb_abs_path).relative_to(config.ASSET_DIR)).replace("\\", "/")
        page = browser.new_page(viewport={"width": 512, "height": 512})
        page.goto(f"http://127.0.0.1:{port}/_render.html")
        page.wait_for_function("document.querySelector('#mv')?.loaded === true", timeout=30000)
        png = page.locator("#mv").screenshot()
        page.close()
        return png

    return capture


def main() -> int:
    import argparse

    from app.database import SessionLocal
    from app.models import Task
    from app.spotlight import find_spotlight

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="spotlight slug to render")
    args = ap.parse_args()
    db = SessionLocal()
    spot = find_spotlight(args.slug)
    if spot is None:
        print(f"no spotlight '{args.slug}'")
        return 1
    task = db.execute(select(Task).where(Task.title == spot["task_title"])).scalars().first()
    if task is None:
        print("subject task not present")
        return 1
    oids = [
        o.id
        for o in db.execute(
            select(ModelOutput).where(
                ModelOutput.task_id == task.id, ModelOutput.is_gold.is_(False)
            )
        ).scalars()
    ]
    res = render_outputs(db, oids, capture=_browser_capture_factory())
    print(res)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
