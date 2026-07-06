"""Best-effort: surface any embedded provenance hints (EXIF Artist/Copyright, matching old MVP
sidecar) for each current reference photo, to help fill its CC provenance sidecar. Fabricates
nothing — untraceable photos are reported so they can be swapped or hand-sourced."""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402


def main() -> int:
    ref = config.ASSET_DIR / "reference"
    for img in sorted(glob.glob(str(ref / "*_ref.jpg"))):
        name = os.path.basename(img)
        hint = "no EXIF"
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS

            ex = Image.open(img)._getexif() or {}
            tags = {
                TAGS.get(k, k): v
                for k, v in ex.items()
                if TAGS.get(k, k) in ("Artist", "Copyright", "ImageDescription")
            }
            hint = str(tags) if tags else "no source EXIF"
        except Exception as e:
            hint = f"unreadable: {e}"
        sidecar = img[:-4] + ".json"
        print(f"{name}: sidecar={'present' if os.path.exists(sidecar) else 'MISSING'} | {hint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
