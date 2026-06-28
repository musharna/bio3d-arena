"""Grade a candidate reference photo as a single-image→3D reconstruction input.

Two layers: deterministic PIL/numpy heuristics (resolution + background uniformity) and a VLM
grader (Task 3, reuses the app/judge.py forced-tool pattern). grade_input combines them. The VLM
branch is skipped when heuristics_only=True or no client is supplied."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

from app.judge import JUDGE_MODEL
from app.morphology import GROWTH_FORMS

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


GRADE_TOOL = {
    "name": "record_input_grade",
    "description": "Grade a single photo as an input for image-to-3D reconstruction of a plant.",
    "input_schema": {
        "type": "object",
        "properties": {
            "growth_form": {"type": "string", "enum": sorted(GROWTH_FORMS)},
            "background_ok": {
                "type": "boolean",
                "description": "Plain/neutral, separable background.",
            },
            "view_matches_recipe": {
                "type": "boolean",
                "description": "View matches the recipe for this form.",
            },
            "fill_ok": {
                "type": "boolean",
                "description": "Subject centered and fills >50% of frame.",
            },
            "verdict": {"type": "string", "enum": ["good", "marginal", "reject"]},
            "reasons": {"type": "string", "description": "One sentence justification."},
        },
        "required": ["growth_form", "background_ok", "view_matches_recipe", "fill_ok", "verdict"],
    },
}


def _grade_img_block(b64: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _build_grade_messages(image_b64: str, growth_form: str, strategy_entry) -> list[dict]:
    recipe = (
        f"Expected growth form: {growth_form}. Recommended capture for this form — view: "
        f"{strategy_entry.capture_view}; {strategy_entry.background}; {strategy_entry.framing}; "
        f">={strategy_entry.min_px}px."
    )
    text = (
        "You are grading ONE photo as the input for single-image to 3D reconstruction of a plant.\n"
        f"{recipe}\n\n"
        "First classify the plant's growth form. Then judge the photo AGAINST THE RECIPE FOR THE "
        "GROWTH FORM YOU OBSERVE: is the background plain/separable, does the view match that "
        "recipe, does the subject fill the frame? Do NOT penalize a top-down view for a rosette — "
        "that is correct for a radially-flat plant. Then call record_input_grade."
    )
    return [
        {"role": "user", "content": [{"type": "text", "text": text}, _grade_img_block(image_b64)]}
    ]


def _parse_grade(response) -> dict:
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", "") == "record_input_grade"
        ):
            d = block.input or {}
            if d.get("growth_form") not in GROWTH_FORMS:
                raise ValueError(f"invalid growth_form: {d.get('growth_form')!r}")
            if d.get("verdict") not in {"good", "marginal", "reject"}:
                raise ValueError(f"invalid verdict: {d.get('verdict')!r}")
            return {
                "growth_form": d["growth_form"],
                "background_ok": bool(d["background_ok"]),
                "view_matches_recipe": bool(d["view_matches_recipe"]),
                "fill_ok": bool(d["fill_ok"]),
                "verdict": d["verdict"],
                "reasons": d.get("reasons", ""),
            }
    raise ValueError("no record_input_grade tool_use block in response")


def grade_with_vlm(client, image_bytes: bytes, *, growth_form: str, strategy_entry) -> dict:
    """One forced-tool VLM call grading the photo against the recipe. Mirrors app.judge.judge_pair."""
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=400,
        tools=[GRADE_TOOL],
        tool_choice={"type": "tool", "name": "record_input_grade"},
        messages=_build_grade_messages(b64, growth_form, strategy_entry),
    )
    return _parse_grade(resp)


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
    if not heuristics_only and client is not None:
        try:
            vlm = grade_with_vlm(
                client, image_bytes, growth_form=growth_form, strategy_entry=strategy_entry
            )
            gf_match = vlm["growth_form"] == growth_form
            if not gf_match:
                reasons.append(f"VLM sees {vlm['growth_form']}, seed says {growth_form}")
            for key in ("background_ok", "view_matches_recipe", "fill_ok"):
                if not vlm[key]:
                    reasons.append(f"VLM: {key} is false")
        except Exception as e:  # noqa: BLE001 — degrade to heuristics; key-safe message
            reasons.append(f"vlm_error: {type(e).__name__}")

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
