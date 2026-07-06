# app/completeness_validation.py
"""Validate the completeness metric against HUMAN labels derived from the trait-level
calibration CSVs.

GT derivation (auditable, human-signal only — never the prior VLM judge's rationale, which
would be circular):
  * Incompleteness categories come from the human free-text `note` via an auditable keyword
    table matched to the ACTUAL note vocabulary (fragment / isolated-organ / partial-organism).
    Specific buckets are checked before generic ones so "not a plant / junk --fruit" (an isolated
    fruit) maps to isolated-organ, not the generic "junk" -> fragment.
  * `complete` is a POSITIVE label: an output with >=1 human `present_correct` trait verdict and
    NO incompleteness note. Never inferred from mere absence of a bad note.
  * Outputs with only trait-quibble notes ("bad trait", "no color", ...) and no present_correct
    verdict are dropped (counted), never coerced to a class.

The robust headline is BINARY complete-vs-incomplete kappa (the PASS gate); the 4-way kappa and
isolated-organ recall are experimental (small per-class n in the calibration corpus)."""

from __future__ import annotations

from app.calibration import cohens_kappa

# Incompleteness keyword table over the REAL human-note vocabulary. Order matters: the FIRST
# matching bucket wins, so the SPECIFIC (isolated / partial) buckets precede the generic
# fragment bucket — otherwise a bare "junk" substring would swallow "junk --fruit".
_KEYWORDS = [
    (
        "isolated-organ",  # a single organ, rest of the plant absent
        [
            "--fruit",
            "/ fruit",
            "just fruit",
            "just a fruit",
            "jhust fruit",  # observed typo in the corpus
            "fruit only",
            "only fruit",
            "only a fruit",
            "only a flower",
            "missing rest of plant",
            "just leaf",
            "only a leaf",
        ],
    ),
    (
        "partial-organism",  # some structure, but not a full plant
        [
            "just sticks",
            "leafless",
            "stub",
            "seedling",
            "partial plant",
            "not a full plant",
            "not full plant",
        ],
    ),
    (
        "fragment",  # unrecognizable junk / no identifiable organ
        ["not a plant", "junk", "garbage", "blob", "unrecognizable", "trash"],
    ),
]


def map_note_to_category(human_verdict: str, note: str) -> str | None:
    """Map a human note to an INCOMPLETENESS category, or None (drop).

    `complete` is intentionally NOT returned here — a complete plant rarely gets an explicit
    "whole plant" note; it is assigned in `gt_by_output` from a `present_correct` trait verdict.
    `human_verdict` is folded into the searched text for robustness but the corpus's verdict
    tokens ("present_correct"/"absent"/"not_assessable") contain no keyword, so only the note
    drives the mapping."""
    text = f"{human_verdict} {note}".lower()
    if not text.strip():
        return None
    for cat, kws in _KEYWORDS:
        if any(k in text for k in kws):
            return cat
    return None  # trait-quibble / ambiguous -> drop from the eval set


_SEVERITY = {
    "fragment": 0,
    "isolated-organ": 1,
    "partial-organism": 2,
    "malformed": 3,
    "complete": 4,
}


def gt_by_output(rows: list[dict]) -> dict[int, str]:
    """Aggregate trait-level rows to one human GT category per output_id.

    An output's category is the WORST (lowest-severity) incompleteness mapped from any of its
    notes. If no note maps to an incompleteness category but the output has >=1 human
    `present_correct` trait verdict, it is labeled `complete` (a positive signal, and one an
    incompleteness note always outranks). Outputs with neither signal are dropped."""
    worst: dict[int, str] = {}
    has_present: set[int] = set()
    for r in rows:
        oid = r["output_id"]
        if (r.get("human_verdict") or "").strip().lower() == "present_correct":
            has_present.add(oid)
        cat = map_note_to_category(r.get("human_verdict", ""), r.get("note", ""))
        if cat is None:
            continue
        if oid not in worst or _SEVERITY[cat] < _SEVERITY[worst[oid]]:
            worst[oid] = cat
    gt = dict(worst)
    for oid in has_present:
        if oid not in gt:  # an incompleteness flag always outranks a present_correct trait
            gt[oid] = "complete"
    return gt


def _binary(cat: str) -> str:
    return "complete" if cat == "complete" else "incomplete"


def agreement(pred: dict[int, str], gt: dict[int, str]) -> dict:
    """kappa (binary complete/incomplete + full 4-way) and isolated-organ recall over the
    outputs present in BOTH pred and gt."""
    oids = [o for o in gt if o in pred]
    dropped = len(gt) - len(oids)
    g4 = [gt[o] for o in oids]
    p4 = [pred[o] for o in oids]
    gb = [_binary(c) for c in g4]
    pb = [_binary(c) for c in p4]
    iso = [o for o in oids if gt[o] == "isolated-organ"]
    iso_hit = sum(1 for o in iso if pred[o] == "isolated-organ")
    return {
        "n": len(oids),
        "binary_kappa": cohens_kappa(gb, pb),
        "fourway_kappa": cohens_kappa(g4, p4),
        "isolated_recall": (iso_hit / len(iso)) if iso else None,
        "dropped": dropped,
    }
