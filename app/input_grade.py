"""Grade a candidate reference photo as a single-image→3D reconstruction input.

Two layers: deterministic PIL/numpy heuristics (resolution + background uniformity) and a VLM
grader (Task 3, reuses the app/judge.py forced-tool pattern). grade_input combines them. The VLM
branch is skipped when heuristics_only=True or no client is supplied."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

_BG_VARIANCE_THRESHOLD = 0.12  # mean per-channel corner std / 255; below = plain background


@dataclass
class GradeResult:
    width: int
    height: int
    dims_ok: bool
    bg_uniformity: float  # mean corner std / 255 (lower = more uniform background)
    bg_ok: bool
    vlm: dict | None  # None when heuristics_only / no client / VLM error
    growth_form_match: bool | None  # None when no VLM result
    verdict: str  # good | marginal | reject
    reasons: list[str] = field(default_factory=list)


def _heuristics(image_bytes: bytes, *, min_px: int) -> tuple[int, int, bool, float, bool]:
    """Return (width, height, dims_ok, bg_uniformity, bg_ok). Samples the 4 corner regions
    (~10% each) and measures colour spread — a plain background has low spread."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = im.size
    dims_ok = min(w, h) >= min_px
    arr = np.asarray(im, dtype=np.float32)
    ch, cw = max(1, h // 10), max(1, w // 10)
    corners = np.concatenate(
        [
            arr[:ch, :cw].reshape(-1, 3),
            arr[:ch, -cw:].reshape(-1, 3),
            arr[-ch:, :cw].reshape(-1, 3),
            arr[-ch:, -cw:].reshape(-1, 3),
        ]
    )
    bg_uniformity = float(corners.std(axis=0).mean() / 255.0)
    bg_ok = bg_uniformity < _BG_VARIANCE_THRESHOLD
    return w, h, dims_ok, bg_uniformity, bg_ok


def _verdict(dims_ok: bool, bg_ok: bool, vlm: dict | None) -> str:
    if not dims_ok:
        return "reject"  # too low-res is disqualifying regardless of content
    if vlm is not None:
        v = vlm["verdict"]
        if v == "good" and not bg_ok:
            return "marginal"  # VLM liked it but corners are busy
        return v
    return "good" if bg_ok else "marginal"


def grade_input(
    image_bytes: bytes,
    *,
    growth_form: str,
    strategy_entry,
    client=None,
    heuristics_only: bool = False,
) -> GradeResult:
    """Grade one photo against the recipe for its growth form. Deterministic heuristics always
    run; the VLM grader runs only when not heuristics_only and a client is supplied (added in
    Task 3). A VLM error is recorded as a reason (type name only) and degrades to heuristics."""
    w, h, dims_ok, bg_uniformity, bg_ok = _heuristics(image_bytes, min_px=strategy_entry.min_px)
    reasons: list[str] = []
    if not dims_ok:
        reasons.append(f"resolution {min(w, h)}px < {strategy_entry.min_px}px")
    if not bg_ok:
        reasons.append("background not plain (high corner colour variance)")

    vlm: dict | None = None
    gf_match: bool | None = None
    # VLM branch is wired in Task 3.

    return GradeResult(
        width=w,
        height=h,
        dims_ok=dims_ok,
        bg_uniformity=bg_uniformity,
        bg_ok=bg_ok,
        vlm=vlm,
        growth_form_match=gf_match,
        verdict=_verdict(dims_ok, bg_ok, vlm),
        reasons=reasons,
    )
