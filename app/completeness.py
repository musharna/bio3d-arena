# app/completeness.py
"""Organism-level biological completeness metric: VLM organ-presence read of a generated
organism's rendered views against its taxon's expected-organ inventory, plus category/score
derivation. Reference-free (no GT). Mirrors the app.input_grade VLM tool-use pattern."""

from __future__ import annotations

import base64
import json

from app.judge import JUDGE_MODEL
from app.organ_inventory import TaxonInventory


def derive(inventory: TaxonInventory, organs_present: list[dict]) -> tuple[str, float]:
    """Map a per-part present/absent checklist to (category, score). Categories: fragment /
    isolated-organ / partial-organism / complete — keyed purely on required part-TYPE presence,
    identically for all kingdoms. score = required part-type coverage.

    A multi-part organ's `complement` status (leg x4, wing x2) is recorded in the checklist as an
    ADVISORY note but does NOT gate the category: firming the animal-completeness scores showed a
    VLM cannot reliably COUNT thin paired limbs from a turntable contact sheet (a correctly
    4-legged dog is routinely reported `missing_some`), so the old `malformed` category promoted
    that measurement noise into a hard verdict — and did so only for animals, since plants/fungi
    have no complement>1 parts. Dropping the gate removes the artifact and the kingdom asymmetry;
    a genuinely missing part-TYPE still surfaces as partial-organism. See
    memory/animal_fidelity_firming_2026-07-17.md."""
    by_key = {o["key"]: o for o in organs_present}
    required = [o for o in inventory.organs if o.required]
    req_present = sum(1 for o in required if by_key.get(o.key, {}).get("status") == "present")
    score = req_present / len(required) if required else 0.0
    present_count = sum(
        1 for o in inventory.organs if by_key.get(o.key, {}).get("status") == "present"
    )
    all_required_present = req_present == len(required)

    if present_count == 0:
        category = "fragment"
    elif all_required_present:
        category = "complete"
    elif present_count == 1:
        category = "isolated-organ"
    else:
        category = "partial-organism"
    return category, score


COMPLETENESS_TOOL = {
    "name": "record_completeness",
    "description": "Record which expected organs are visible in the rendered model.",
    "input_schema": {
        "type": "object",
        "properties": {
            "organs_present": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "status": {"type": "string", "enum": ["present", "absent", "uncertain"]},
                        "complement": {
                            "type": "string",
                            "enum": ["full", "missing_some", "extra", "uncertain"],
                        },
                    },
                    "required": ["key", "status"],
                },
            },
            "note": {"type": "string"},
        },
        "required": ["organs_present", "note"],
    },
}


def _img_block(png: bytes) -> dict:
    b64 = base64.b64encode(png).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _build_messages(png: bytes, inventory: TaxonInventory) -> list[dict]:
    lines = "\n".join(
        f"- {o.key}: {o.visual}" + (f" (expect {o.complement})" if o.complement > 1 else "")
        for o in inventory.organs
    )
    text = (
        f"This is a contact sheet of a generated 3D model of {inventory.taxon}, "
        "rendered from several angles. For EACH expected part below, mark whether it is visibly "
        "present in the model (present / absent / uncertain). For any part with an expected count "
        "(e.g. 'expect 4'), ALSO set `complement`: `full` if the whole set is present, "
        "`missing_some` if one or more are clearly missing, `extra` if there are clearly more than "
        "expected, or `uncertain`. Do NOT count exactly — judge whether the full set is there. "
        "Judge only what you can see; a rendering of a single detached part should mark the others "
        f"absent.\n\nExpected parts:\n{lines}\n\nThen call record_completeness."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img_block(png)]}]


def _parse(response) -> dict:
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", "") == "tool_use"
            and getattr(block, "name", "") == "record_completeness"
        ):
            inp = block.input
            return {"organs_present": inp.get("organs_present", []), "note": inp.get("note", "")}
    raise ValueError("no record_completeness tool_use block in response")


def score_completeness(client, sheet_png: bytes, *, inventory: TaxonInventory) -> dict:
    """One VLM call over the contact sheet; returns the parsed organ checklist + note."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=500,
        tools=[COMPLETENESS_TOOL],
        tool_choice={"type": "tool", "name": "record_completeness"},
        messages=_build_messages(sheet_png, inventory),
    )
    return _parse(resp)


def upsert_completeness(
    db,
    output_id: int,
    *,
    category: str,
    score: float | None,
    checklist: dict,
    judge_model: str,
    scorer_version: str,
):
    """Insert or overwrite the single Completeness row for an output. Caller commits."""
    from app.models import Completeness

    row = db.query(Completeness).filter_by(output_id=output_id).one_or_none()
    if row is None:
        row = Completeness(output_id=output_id)
        db.add(row)
    row.category = category
    row.score = score
    row.checklist_json = json.dumps(checklist)
    row.judge_model = judge_model
    row.scorer_version = scorer_version
    return row


def enumerate_completeness_work(db, task_ids) -> list[dict]:
    """One row per eligible output (non-gold, non-reference-scan, non-untextured) of tasks
    that HAVE a TraitRubric with an inventory-covered taxon. Mirrors trait_judge.enumerate_work."""
    from app.models import Task, TraitRubric
    from app.organ_inventory import inventory_for
    from app.sourcing import is_reference_scan, is_untextured_output

    items = []
    for tid in task_ids:
        rubric = db.query(TraitRubric).filter_by(task_id=tid).first()
        if rubric is None or inventory_for(rubric.taxon) is None:
            continue
        task = db.get(Task, tid)
        if task is None:
            continue
        for out in task.outputs:
            if out.is_gold or is_reference_scan(out.source) or is_untextured_output(out):
                continue
            items.append({"output_id": out.id, "taxon": rubric.taxon})
    return items


def score_outputs(db, work, *, client, sheet_for, scorer_version: str) -> dict:
    """Score each work row: get its contact sheet (injected sheet_for), VLM-check, derive,
    upsert. Fail-loud per output (recorded, loop continues). Caller commits."""
    from app.organ_inventory import inventory_for

    scored = skipped = errors = 0
    failures = []
    seen = set()
    for item in work:
        oid = item["output_id"]
        if oid in seen:
            continue
        seen.add(oid)
        inv = inventory_for(item["taxon"])
        if inv is None:
            skipped += 1
            continue
        try:
            png = sheet_for(oid)
            result = score_completeness(client, png, inventory=inv)
            category, score = derive(inv, result["organs_present"])
            upsert_completeness(
                db,
                oid,
                category=category,
                score=score,
                checklist=result,
                judge_model=JUDGE_MODEL,
                scorer_version=scorer_version,
            )
            scored += 1
        except Exception as e:  # fail-loud per output, do not abort the batch
            errors += 1
            failures.append({"output_id": oid, "error": repr(e)})
    return {
        "scored": scored,
        "skipped_no_inventory": skipped,
        "errors": errors,
        "failures": failures,
    }


def recon_reliability_flags(db, *, gap_threshold: float = 0.4) -> list[dict]:
    """Input/capture-quality triage: per taxon, compare mean organism-completeness of image_recon
    outputs vs text_native outputs. text→3D shares a task's SUBJECT (the prompt) but NOT its
    reference photo, so when recon completeness is far below text completeness the recon *input*
    (reference photo / capture geometry) is suspect — the signal that would have auto-caught the
    Cucurbita reference-photo bug (recon 0.13 vs text 1.00). A flag is TRIAGE ("inspect this
    taxon's reference/capture"), not a diagnosis: a genuinely hard taxon can also trip it, and the
    right response either way is to look. Only taxa with ≥1 completeness-scored output in BOTH
    paradigms are comparable; others are omitted (can't compare). Read-only."""
    import collections

    from app.models import Completeness, Generator, ModelOutput, TraitRubric

    taxon_by_task = {r.task_id: r.taxon for r in db.query(TraitRubric).all()}
    paradigm_by_gen = {g.id: (g.paradigm or "") for g in db.query(Generator).all()}
    scores: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    for c in db.query(Completeness).all():
        if c.score is None:
            continue
        out = db.get(ModelOutput, c.output_id)
        if out is None or out.hidden_at is not None:
            continue  # withdrawn output: not served, and may be from a since-replaced input photo
        taxon = taxon_by_task.get(out.task_id)
        pgm = paradigm_by_gen.get(out.generator_id)
        if taxon is None or pgm not in ("image_recon", "text_native"):
            continue
        scores[(taxon, pgm)].append(c.score)

    flags = []
    for taxon in {t for (t, _p) in scores}:
        recon = scores.get((taxon, "image_recon"), [])
        text = scores.get((taxon, "text_native"), [])
        if not recon or not text:
            continue  # need both paradigms to compare
        recon_mean = sum(recon) / len(recon)
        text_mean = sum(text) / len(text)
        gap = text_mean - recon_mean
        flags.append(
            {
                "taxon": taxon,
                "recon_mean": recon_mean,
                "n_recon": len(recon),
                "text_mean": text_mean,
                "n_text": len(text),
                "gap": gap,
                "flag": gap >= gap_threshold,
            }
        )
    flags.sort(key=lambda r: r["gap"], reverse=True)
    return flags
