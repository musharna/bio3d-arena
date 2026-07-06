"""Render ONE output's contact sheet to its cache path, in an isolated process with a fresh
browser, then exit. Meant to be invoked as a subprocess with a hard OS-level timeout: a
pathological GLB can WEDGE the model-viewer/chromium renderer in a way that neither playwright's
page timeout nor an in-process try/except can recover (the browser stops responding entirely).
Isolating each render in its own short-lived process lets the parent `subprocess.run(timeout=...)`
force-kill a wedge and skip that output instead of stalling the whole batch.

Usage:  python scripts/render_one_sheet.py <output_id> <condition>
Exit 0 if the sheet exists on disk afterward, 1 otherwise.
"""

from __future__ import annotations

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.judge_render import contact_sheet_path, render_contact_sheets  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: render_one_sheet.py <output_id> <condition>", file=sys.stderr)
        return 2
    oid = int(sys.argv[1])
    condition = sys.argv[2]

    from scripts.judge_capture import browser_capture_multi_factory

    capture_multi = browser_capture_multi_factory()
    with SessionLocal() as db:
        render_contact_sheets(db, [oid], condition, capture_multi=capture_multi)

    abs_path = Path(config.ASSET_DIR) / contact_sheet_path(oid, condition)
    ok = abs_path.exists() and abs_path.stat().st_size > 0
    print(f"render_one_sheet {oid} {condition}: {'OK' if ok else 'FAILED'}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
