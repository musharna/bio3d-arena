# app/dgen.py
"""Rubric-in-the-loop self-improving generation (D-Gen).

Closes the loop between the arena's evaluators (trait-morphology rubric + completeness metric)
and the LLM-procedural generator: generate -> render the GLB directly -> score -> build an
actionable critique -> feed the previous script + critique back -> regenerate, up to N rounds
per taxon, keeping the best round. All LLM/Blender/VLM/browser access is behind injected seams."""

from __future__ import annotations

import base64

from app.commission import build_prompt


def fidelity(trait_results: list[dict]) -> tuple[float | None, int, int]:
    """(fidelity, n_correct, n_assessable) from check_traits output. `assessable` excludes
    verdict == 'not_assessable'; fidelity = present_correct / assessable, or None if none assessable."""
    assessable = [t for t in trait_results if t.get("verdict") != "not_assessable"]
    n_correct = sum(1 for t in assessable if t.get("verdict") == "present_correct")
    n_assessable = len(assessable)
    if n_assessable == 0:
        return None, n_correct, 0
    return n_correct / n_assessable, n_correct, n_assessable


def build_critique(
    trait_results: list[dict],
    traits: list[dict],
    completeness: dict,
    run_status: str,
    run_error: str,
) -> str:
    """Actionable instruction list fed into the NEXT round. Empty only if nothing to fix."""
    expected_by_key = {t["key"]: t.get("expected", "") for t in traits}
    lines: list[str] = []
    if run_status and run_status != "ok":
        lines.append(
            f"The script failed to run (status: {run_status}). Fix this error:\n"
            f"{(run_error or '')[:1500]}"
        )
    for t in trait_results:
        v = t.get("verdict")
        if v in ("absent", "present_wrong"):
            key = t.get("trait_key", "")
            problem = "missing" if v == "absent" else "botanically wrong"
            lines.append(f"FIX {key} ({problem}): expected {expected_by_key.get(key, '')}")
    cat = (completeness or {}).get("category")
    if cat and cat != "complete":
        missing = (
            ", ".join((completeness or {}).get("missing_organs") or []) or "the vegetative body"
        )
        lines.append(
            f"The plant is not complete (currently: {cat}); build the whole plant body "
            f"(missing: {missing})."
        )
    return "\n".join(lines)


def build_refine_prompt(species: str, common: str, prev_script: str, critique: str) -> str:
    """base build_prompt + the previous script + the critique + output-only instruction."""
    return (
        build_prompt(species, common) + "\n\n--- REVISION ROUND ---\n"
        "Here is your previous script:\n```python\n"
        + prev_script
        + "\n```\n\nIt was rendered from several angles and evaluated. Fix EXACTLY these issues:\n"
        + critique
        + "\n\nOutput ONLY the revised complete Python script — no explanation, no markdown prose."
    )


def score_glb(
    glb_path,
    *,
    taxon: str,
    prompt: str,
    traits: list[dict],
    capture_multi,
    trait_client,
    completeness_client,
    condition: str = "multi4",
) -> dict:
    """Render the GLB directly (capture_multi -> tile_contact_sheet), then run the trait judge and
    the completeness scorer over the sheet. Returns fidelity + trait_results + completeness. The
    caller records failures; a render/judge exception propagates."""
    from app.completeness import derive, score_completeness
    from app.judge_render import CONDITIONS, tile_contact_sheet
    from app.organ_inventory import inventory_for
    from app.traits import check_traits

    spec = CONDITIONS[condition]
    pngs = capture_multi(str(glb_path), spec["azimuths"], spec["elev"])
    sheet_png = tile_contact_sheet(pngs, spec["cols"], spec["rows"])
    sheet_b64 = base64.b64encode(sheet_png).decode()

    trait_results = check_traits(
        trait_client, species=taxon, prompt=prompt, sheet_b64=sheet_b64, traits=traits
    )
    fid, n_correct, n_assessable = fidelity(trait_results)

    inv = inventory_for(taxon)
    comp = score_completeness(completeness_client, sheet_png, inventory=inv)
    category, score = derive(inv, comp["organs_present"])
    present = {o["key"] for o in comp["organs_present"] if o.get("status") == "present"}
    missing = [o.key for o in inv.organs if o.required and o.key not in present]

    return {
        "fidelity": fid,
        "n_correct": n_correct,
        "n_assessable": n_assessable,
        "trait_results": trait_results,
        "completeness_category": category,
        "completeness_score": score,
        "completeness_missing_organs": missing,
        "sheet_png": sheet_png,
    }
