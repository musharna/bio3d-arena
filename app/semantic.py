# app/semantic.py
"""Semantic-admissibility predicate: one VLM tool-use call over an output's turntable contact
sheet judging whether it is a single, whole, valid ORGANISM specimen (admissibility only — NOT a
fidelity/species check). Rejects cardinality/kind failures (multiple organisms, a detached part,
junk that is not a plausible organism) that structural geometry cannot see.
Precision-first: uncertain (and any unmapped code) -> admit. Clones
app.completeness's VLM-judge shape; persistence reuses app.structural.upsert_verdict
(predicate='semantic', no schema change)."""

from __future__ import annotations

import base64

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import flags
from .admissibility import Verdict
from .judge import JUDGE_MODEL
from .models import Admissibility, ModelOutput, TraitRubric
from .sourcing import is_reference_scan, is_untextured_output

VERSION = "semantic-v2"

# admit iff the VLM code is NOT one of these — so ok, uncertain, and any unrecognized
# code all admit (precision-first; a false-reject silently biases the ranking). wrong_species
# was dropped after the acceptance run (0 flags caught, 3 of 4 true FPs); a stale model that
# still emits it now falls through to admit.
REJECT_CODES = {"multiple", "sub_part", "not_the_organism"}

# Taxa whose natural unit is a cluster/colony (modular organisms: bracket/shelf fungi; future
# coral / colonial animals). For these a same-species cluster is ONE valid subject — the gate must
# not flag it `multiple`. Unitary organisms (a discrete individual) are not listed.
COLONIAL_TAXA = frozenset({"Trametes versicolor"})

# Advisory flags use one synthetic session id (record_flag is idempotent per (output, session_id)
# and requires a non-null id) and a sentinel threshold so an advisory flag NEVER auto-hides the
# output — advisory surfaces to the review queue; it does not remove from the pool (that is gating).
SEMANTIC_FLAG_SESSION = "semantic-v2"
ADVISORY_NO_HIDE_THRESHOLD = 10**9

SEMANTIC_TOOL = {
    "name": "record_admissibility",
    "description": "Judge whether the rendered model is a single, whole, valid specimen of the target organism.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["ok", "multiple", "sub_part", "not_the_organism", "uncertain"],
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
    of_taxon = f", intended to be {taxon}," if taxon else ""
    colonial_clause = (
        " NOTE: this organism naturally grows in clusters/colonies (overlapping shelf/bracket "
        "fungi) — a cluster of the same species is its natural form and is a SINGLE valid subject; "
        "do NOT call that `multiple`."
        if taxon in COLONIAL_TAXA
        else ""
    )
    text = (
        f"This is a contact sheet of a generated 3D model{of_taxon} rendered from several angles on "
        "a neutral gray background. This is an ADMISSIBILITY check ONLY — judge whether the model is "
        "a SINGLE, WHOLE organism suitable to vote on. It is NOT a quality or fidelity check: a "
        "low-fidelity, crude, or inaccurate rendering that is still recognizably a single whole "
        "living thing is ADMISSIBLE (`ok`) — voters judge fidelity, not you. "
        "Reject ONLY as: `multiple` (clearly more than one DISTINCT organism, or a cluttered scene "
        f"with unrelated distractor objects);{colonial_clause} "
        "`sub_part` (only a detached part — a single organ or appendage on its own — not a whole "
        "organism); "
        "`not_the_organism` (NOT a plausible whole organism at all — junk or broken geometry, a "
        "featureless blob, or an unrelated everyday object; do NOT use this merely because the shape "
        "is a poor or inaccurate depiction of the intended organism). "
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


def enumerate_semantic_work(db: Session) -> list[dict]:
    """One {'output_id', 'taxon'} per eligible output lacking a current-VERSION semantic verdict.
    Eligible = non-gold, non-reference-scan, non-untextured (structural's breadth — NOT gated on a
    taxon inventory, so this reaches the outputs completeness never scored). taxon = the output's
    task's TraitRubric.taxon if a rubric exists, else None (used only to frame the prompt — every
    reject code is taxon-agnostic, so a missing taxon changes nothing about admissibility)."""
    have = {
        oid
        for (oid,) in db.execute(
            select(Admissibility.output_id).where(
                Admissibility.predicate == "semantic", Admissibility.version == VERSION
            )
        ).all()
    }
    taxon_by_task = {
        tid: taxon
        for (tid, taxon) in db.execute(select(TraitRubric.task_id, TraitRubric.taxon)).all()
    }
    work: list[dict] = []
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    for out in outs:
        if out.id in have:
            continue
        if is_reference_scan(out.source) or is_untextured_output(out):
            continue
        work.append({"output_id": out.id, "taxon": taxon_by_task.get(out.task_id)})
    return work


def evaluate_outputs(db: Session, work, *, client, sheet_for, emit_flags: bool) -> dict:
    """Score each work row and upsert its semantic verdict (persistence is UNCONDITIONAL — the
    acceptance run reads these rows regardless of mode). Fail-loud per output: an unreadable sheet
    or a VLM error is recorded and the batch continues. If emit_flags AND the verdict rejects, also
    record a NON-HIDING advisory OutputFlag (sentinel threshold) so a human sees it in the ⚑ queue.
    Caller commits."""
    from .structural import upsert_verdict  # function-local: avoids importing numpy in Task-1 core

    scored = errors = flagged = 0
    failures: list[dict] = []
    seen: set[int] = set()
    for item in work:
        oid = item["output_id"]
        if oid in seen:
            continue
        seen.add(oid)
        try:
            png = sheet_for(oid)
            result = score_semantic(client, png, taxon=item.get("taxon"))
            verdict = verdict_from_code(result["verdict"], result.get("note", ""))
            upsert_verdict(db, oid, "semantic", verdict, VERSION)
            scored += 1
            if emit_flags and not verdict.admit:
                flags.record_flag(
                    db, oid, SEMANTIC_FLAG_SESSION, verdict.reason, ADVISORY_NO_HIDE_THRESHOLD
                )
                flagged += 1
        except Exception as e:  # noqa: BLE001 — fail-loud per output, never abort the batch
            errors += 1
            failures.append({"output_id": oid, "error": repr(e)})
    return {"scored": scored, "errors": errors, "flagged": flagged, "failures": failures}


class SemanticPredicate:
    name = "semantic"
    version = VERSION

    def rejected_output_ids(self, db: Session) -> set[int]:
        return {
            oid
            for (oid,) in db.execute(
                select(Admissibility.output_id).where(
                    Admissibility.predicate == "semantic", Admissibility.admit.is_(False)
                )
            ).all()
        }
