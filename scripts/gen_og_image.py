"""Generate the default social-share (Open Graph) image → app/static/og-default.png.

A 1200×630 branded card matching the v2 design tokens (dark bg, green accent). Committed as a
static asset; re-run this only to regenerate. Fonts are resolved at generation time (DejaVu via
matplotlib or the system) — the runtime never needs them."""

from __future__ import annotations

import glob
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "og-default.png"
W, H = 1200, 630
BG = (18, 22, 31)  # ~ --bg oklch(0.165 0.018 258)
PANEL = (27, 32, 43)
ACCENT = (70, 201, 138)  # ~ --accent green
TEXT = (232, 236, 242)
MUTED = (150, 160, 176)


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for pat in (
        f"/home/mjarnold/miniconda3/lib/python3.13/site-packages/matplotlib/mpl-data/fonts/ttf/{name}",
        f"/usr/share/fonts/truetype/dejavu/{name}",
        f"/usr/share/fonts/**/{name}",
    ):
        hits = glob.glob(pat, recursive=True)
        if hits:
            return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # subtle top panel band + accent rule
    d.rectangle([0, 0, W, 8], fill=ACCENT)
    m = 84  # margin
    # eyebrow: accent dot + wordmark
    d.ellipse([m, m + 6, m + 22, m + 28], fill=ACCENT)
    d.text((m + 36, m), "BIO 3D ARENA", font=_font(True, 30), fill=TEXT)
    # headline
    d.text((m, m + 96), "Which model rebuilds", font=_font(True, 78), fill=TEXT)
    d.text((m, m + 188), "life best?", font=_font(True, 78), fill=ACCENT)
    # subhead
    d.text(
        (m, m + 306),
        "A blind benchmark arena for 3D generative models",
        font=_font(False, 34),
        fill=MUTED,
    )
    d.text((m, m + 352), "of real organisms.", font=_font(False, 34), fill=MUTED)
    # footer chips
    d.text((m, H - m - 8), "Plants   ·   Fungi   ·   Animals", font=_font(True, 30), fill=ACCENT)
    img.save(OUT, "PNG")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
