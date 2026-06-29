"""Multi-view contact-sheet rendering for the VLM judge.

Pure logic + an injected `capture_multi`; no browser import here (the Playwright
driver lives in scripts/judge_capture.py). Sheets cache to disk by convention
`renders/{output_id}_{condition}.png` under ASSET_DIR, idempotently."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from . import config
from .models import ModelOutput

logger = logging.getLogger(__name__)

CONDITIONS: dict[str, dict] = {
    "single": {"azimuths": [30], "elev": 75, "cols": 1, "rows": 1},
    "multi4": {"azimuths": [0, 90, 180, 270], "elev": 70, "cols": 2, "rows": 2},
    "turntable": {
        "azimuths": [0, 45, 90, 135, 180, 225, 270, 315],
        "elev": 70,
        "cols": 4,
        "rows": 2,
    },
}


def contact_sheet_path(output_id: int, condition: str) -> str:
    return f"renders/{output_id}_{condition}.png"


def tile_contact_sheet(pngs: list[bytes], cols: int, rows: int) -> bytes:
    """Composite PNG bytes row-major into a single PNG. All tiles assumed same size."""
    tiles = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
    tw, th = tiles[0].size
    sheet = Image.new("RGB", (cols * tw, rows * th), (111, 118, 124))  # match render gray
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet.paste(tile, (c * tw, r * th))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def render_contact_sheets(db, output_ids: list[int], condition: str, *, capture_multi) -> dict:
    """Render+tile a contact sheet per output for `condition`. Idempotent: skips
    outputs whose sheet already exists. `capture_multi(glb_abs, azimuths, elev) ->
    list[bytes]` (one PNG per azimuth) is injected (Playwright in prod, stub in tests)."""
    spec = CONDITIONS[condition]
    renders_dir = Path(config.ASSET_DIR) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    rendered = 0
    failures: list[dict] = []  # {oid, error} per output that failed — surfaced, not swallowed
    for oid in output_ids:
        rel = contact_sheet_path(oid, condition)
        abs_path = Path(config.ASSET_DIR) / rel
        if abs_path.exists() and abs_path.stat().st_size > 0:
            continue  # idempotent
        try:
            out = db.get(ModelOutput, oid)
            if out is None:
                raise LookupError(f"ModelOutput {oid} not found")
            glb_abs = str(Path(config.ASSET_DIR) / out.asset_path)
            tiles = capture_multi(glb_abs, spec["azimuths"], spec["elev"])
            sheet = tile_contact_sheet(tiles, spec["cols"], spec["rows"])
            abs_path.write_bytes(sheet)
            rendered += 1
        except Exception as exc:  # noqa: BLE001 — best-effort batch: log + record, don't abort
            logger.warning(
                "contact-sheet render failed for output %s (%s): %s",
                oid,
                condition,
                exc,
                exc_info=True,
            )
            failures.append({"oid": oid, "error": f"{type(exc).__name__}: {exc}"})
    return {"rendered": rendered, "errors": len(failures), "failures": failures}
