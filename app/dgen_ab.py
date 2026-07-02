# app/dgen_ab.py
"""Independent cross-judge A/B for D-Gen firming. A different-lab VLM blind-compares each taxon's
round-0 baseline vs refined-best contact sheet (composited into one A|B image), run in both orders
to cancel position bias, to escape the same-(claude-sonnet)-judge circularity of the D-Gen fidelity
signal. Pure image/parse/verdict/aggregate logic; the VLM call (vision_fn) is injected."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

_LABEL_H = 16


def composite_ab(sheet_left: bytes, sheet_right: bytes) -> bytes:
    """One side-by-side PNG: left half labeled 'A', right half 'B'. Halves are scaled to a common
    height so the judge sees a fair pair."""
    a = Image.open(io.BytesIO(sheet_left)).convert("RGB")
    b = Image.open(io.BytesIO(sheet_right)).convert("RGB")
    h = max(a.height, b.height)
    if a.height != h:
        a = a.resize((round(a.width * h / a.height), h))
    if b.height != h:
        b = b.resize((round(b.width * h / b.height), h))
    canvas = Image.new("RGB", (a.width + b.width, h + _LABEL_H), (255, 255, 255))
    canvas.paste(a, (0, _LABEL_H))
    canvas.paste(b, (a.width, _LABEL_H))
    draw = ImageDraw.Draw(canvas)
    draw.text((a.width // 2, 2), "A", fill=(0, 0, 0))
    draw.text((a.width + b.width // 2, 2), "B", fill=(0, 0, 0))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def ab_prompt(taxon: str, common: str) -> str:
    return (
        f"Two rendered 3D models of a {common} ({taxon}) are shown side by side: model A on the "
        f"left, model B on the right. Judge which is a more complete and botanically accurate whole "
        f"{common} plant (correct organs present, right overall structure). Reply with a single "
        f'letter only: "A" or "B".'
    )


def _parse_ab(resp: str | None) -> str | None:
    t = (resp or "").strip().upper()
    if not t:
        return None
    if t[0] in ("A", "B"):
        return t[0]
    # fall back: last standalone A/B token
    for tok in reversed(t.replace(".", " ").replace(",", " ").split()):
        if tok in ("A", "B"):
            return tok
    return None


def judge_pair(vision_fn, comp_png: bytes, taxon: str, common: str) -> str | None:
    """vision_fn(prompt, image_png) -> str. Returns 'A'|'B'|None."""
    return _parse_ab(vision_fn(ab_prompt(taxon, common), comp_png))


def verdict_both_orders(pick1: str | None, pick2: str | None) -> str:
    """pick1 = pick when baseline=A (refined='B'); pick2 = pick when baseline=B (refined='A')."""
    if pick1 == "B" and pick2 == "A":
        return "refined"
    if pick1 == "A" and pick2 == "B":
        return "baseline"
    return "inconsistent"


def aggregate(rows: list[dict]) -> dict:
    ab = [r for r in rows if r.get("bucket") == "ab"]
    refined = sum(1 for r in ab if r.get("verdict") == "refined")
    baseline = sum(1 for r in ab if r.get("verdict") == "baseline")
    inconsistent = sum(1 for r in ab if r.get("verdict") == "inconsistent")
    n_ab = len(ab)
    return {
        "n_ab": n_ab,
        "refined": refined,
        "baseline": baseline,
        "inconsistent": inconsistent,
        "refined_rate": (refined / n_ab) if n_ab else None,
        "repairs": sum(1 for r in rows if r.get("bucket") == "repair"),
        "no_refinement": sum(1 for r in rows if r.get("bucket") == "no-refinement"),
    }
