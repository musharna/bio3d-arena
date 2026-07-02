# app/completeness.py
"""Organism-level biological completeness metric: VLM organ-presence read of a generated
plant's rendered views against its taxon's expected-organ inventory, plus category/score
derivation. Reference-free (no GT). Mirrors the app.input_grade VLM tool-use pattern."""

from __future__ import annotations

from app.organ_inventory import TaxonInventory


def derive(inventory: TaxonInventory, organs_present: list[dict]) -> tuple[str, float]:
    """Map a per-organ present/absent/uncertain checklist to (category, score).

    Required organs = the vegetative body; score = required-present / required-total.
    Categories are total + mutually exclusive over present_count in {0, 1, >=2}."""
    status = {o["key"]: o.get("status") for o in organs_present}
    required = [o.key for o in inventory.organs if o.required]
    req_present = sum(1 for k in required if status.get(k) == "present")
    score = req_present / len(required) if required else 0.0
    present_count = sum(1 for v in status.values() if v == "present")

    if present_count == 0:
        category = "fragment"
    elif present_count == 1:
        category = "isolated-organ"
    elif req_present == len(required):
        category = "complete"
    else:
        category = "partial-organism"
    return category, score
