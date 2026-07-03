# app/semantic.py
"""Semantic-admissibility predicate: one VLM tool-use call over an output's turntable contact
sheet judging whether it is a single, whole, valid plant specimen. Rejects cardinality/identity
failures (multiple plants, a detached organ, a non-plant, the wrong species) that structural
geometry cannot see. Precision-first: uncertain (and any unmapped code) -> admit. Clones
app.completeness's VLM-judge shape; persistence reuses app.structural.upsert_verdict
(predicate='semantic', no schema change)."""

from __future__ import annotations

import base64

from .admissibility import Verdict
from .judge import JUDGE_MODEL

VERSION = "semantic-v1"

ADMIT_CODES = {"ok", "uncertain"}
REJECT_CODES = {"multiple", "sub_part", "not_a_plant", "wrong_species"}

# Advisory flags use one synthetic session id (record_flag is idempotent per (output, session_id)
# and requires a non-null id) and a sentinel threshold so an advisory flag NEVER auto-hides the
# output — advisory surfaces to the review queue; it does not remove from the pool (that is gating).
SEMANTIC_FLAG_SESSION = "semantic-v1"
ADVISORY_NO_HIDE_THRESHOLD = 10**9

SEMANTIC_TOOL = {
    "name": "record_admissibility",
    "description": "Judge whether the rendered model is a single, whole, valid plant specimen.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["ok", "multiple", "sub_part", "not_a_plant", "wrong_species", "uncertain"],
            },
            "note": {"type": "string"},
        },
        "required": ["verdict", "note"],
    },
}


def verdict_from_code(code: str, note: str = "") -> Verdict:
    """Map a VLM verdict code to an admissibility Verdict. admit iff code not in REJECT_CODES —
    so ok, uncertain, AND any unrecognized code admit (precision-first: never reject on a code we
    cannot map)."""
    admit = code not in REJECT_CODES
    reason = "" if admit else code
    return Verdict(admit, reason, {"code": code, "note": note})


def _img_block(png: bytes) -> dict:
    b64 = base64.b64encode(png).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _build_messages(png: bytes, taxon: str | None) -> list[dict]:
    of_taxon = f" of {taxon}" if taxon else ""
    wrong_species = f"`wrong_species` (a plant, but clearly not a {taxon}), " if taxon else ""
    text = (
        f"This is a contact sheet of a generated 3D model{of_taxon}, rendered from several angles "
        "on a neutral gray background. Judge whether it is a SINGLE, WHOLE, VALID plant specimen. "
        "Reject as: `multiple` (more than one distinct plant, or a scene/cluster), "
        "`sub_part` (only a detached organ — a single fruit, leaf, or flower — not a whole plant), "
        "`not_a_plant` (not a recognizable plant at all — a blob or non-plant object), "
        f"{wrong_species}"
        "Otherwise answer `ok`. If you genuinely cannot tell, answer `uncertain`. "
        "Reject ONLY when clearly inadmissible; when in doubt, prefer `ok` or `uncertain`. "
        "Then call record_admissibility."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img_block(png)]}]


def _parse(response) -> dict:
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", "") == "tool_use"
            and getattr(block, "name", "") == "record_admissibility"
        ):
            inp = block.input or {}
            return {"verdict": inp.get("verdict", "uncertain"), "note": inp.get("note", "")}
    raise ValueError("no record_admissibility tool_use block in response")


def score_semantic(client, sheet_png: bytes, *, taxon: str | None) -> dict:
    """One VLM call over the contact sheet; returns {'verdict': str, 'note': str}."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=300,
        tools=[SEMANTIC_TOOL],
        tool_choice={"type": "tool", "name": "record_admissibility"},
        messages=_build_messages(sheet_png, taxon),
    )
    return _parse(resp)
