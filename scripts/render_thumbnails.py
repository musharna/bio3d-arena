"""Render a card thumbnail (PNG) for each votable GLB via headless model-viewer.

Drives the SAME <model-viewer> the app uses: navigates to an app page (same origin, so the
GLB loads without CORS — set_content's null origin is blocked), injects a model-viewer pointing
at /assets/<path>, waits for its 'load' event, screenshots the element, and stores it via
app.thumbnails.store_thumbnail (→ Critique.render_path). Requires the server running.

Usage: BIO3D_DATABASE_URL=… BIO3D_DATA_DIR=… python scripts/render_thumbnails.py [--limit N] [--force] [--base URL]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Critique, ModelOutput  # noqa: E402
from app.storage import get_storage  # noqa: E402
from app.thumbnails import store_thumbnail, thumbnail_rel_path  # noqa: E402

INJECT = """
async (asset) => {
  document.body.innerHTML =
    '<model-viewer id="thumb" style="width:400px;height:400px;background:#0d1117" ' +
    'camera-controls exposure="1" shadow-intensity="0.6" src="/assets/' + asset + '"></model-viewer>';
  const m = document.getElementById('thumb');
  await new Promise((res) => {
    m.addEventListener('load', res, { once: true });
    m.addEventListener('error', res, { once: true });
    setTimeout(res, 20000);
  });
  return m.loaded === true;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="cap number rendered (0 = all)")
    ap.add_argument("--force", action="store_true", help="re-render even if a thumbnail exists")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="running server base URL")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    store = get_storage()
    db = SessionLocal()
    rendered = skipped = errors = 0
    try:
        outs = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.is_gold.is_(False), ModelOutput.asset_format == "glb"
                )
            )
            .scalars()
            .all()
        )
        todo = []
        for o in outs:
            if not args.force:
                crit = (
                    db.execute(select(Critique).where(Critique.output_id == o.id)).scalars().first()
                )
                if crit and crit.render_path and store.exists(crit.render_path):
                    skipped += 1
                    continue
            todo.append(o)
        if args.limit:
            todo = todo[: args.limit]
        print(
            f"{len(outs)} GLB outputs · {skipped} already have thumbnails · rendering {len(todo)}"
        )

        with sync_playwright() as p:
            b = p.chromium.launch(
                args=[
                    "--use-angle=swiftshader",
                    "--ignore-gpu-blocklist",
                    "--enable-unsafe-swiftshader",
                ]
            )
            pg = b.new_page(viewport={"width": 420, "height": 420})
            # Must be a page that actually LOADS model-viewer. base.html gates the viewer
            # bundle behind the `viewer_assets` block, so data-dense pages (methodology,
            # leaderboard, models, …) deliberately omit it. This used to point at
            # /methodology: after that split the injected <model-viewer> never upgraded,
            # so it had no layout box and every screenshot() failed "element is not
            # visible" after a 30s timeout — 0 thumbnails written, silently, for the
            # whole batch. /arena mounts a viewer, so the bundle is present.
            pg.goto(f"{args.base}/arena", wait_until="domcontentloaded")
            # Fail loud rather than time out once per output if that ever changes again.
            pg.wait_for_function("() => !!customElements.get('model-viewer')", timeout=15000)
            for o in todo:
                try:
                    loaded = pg.evaluate(INJECT, o.asset_path)
                    pg.wait_for_timeout(900)
                    png = pg.locator("#thumb").screenshot()
                    if not loaded:
                        print(f"  warn  id={o.id} model not loaded (captured anyway)")
                    store_thumbnail(db, o, png, storage=store)
                    db.commit()
                    rendered += 1
                    if rendered % 25 == 0:
                        print(f"  …{rendered} rendered")
                except Exception as e:  # noqa: BLE001 — one bad asset never aborts the batch
                    print(f"  error id={o.id} {o.asset_path}: {e}")
                    errors += 1
                    db.rollback()
            b.close()
    finally:
        db.close()
    print(f"done: rendered {rendered}, skipped {skipped}, errors {errors}")
    print(f"(thumbnails stored under {thumbnail_rel_path(0).rsplit('/', 1)[0]}/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
