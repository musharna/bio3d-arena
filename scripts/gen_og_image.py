"""Generate the default social-share (Open Graph) image → app/static/og-default.png.

The 1200×630 branded card for pages that have no card of their own. It is drawn with the SHARED
vocabulary in `app.og` (v2 tokens, DejaVu, the accent-dot wordmark) — the same helpers the live
per-model card at `/og/models/{slug}.png` uses, so the two are unmistakably the same product.

This one is committed as a static asset (it never goes stale — no per-model data on it); re-run
this only to regenerate it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import og  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "og-default.png"


def main() -> None:
    og.render_default_card().save(OUT, "PNG")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
