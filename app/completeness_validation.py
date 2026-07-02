# app/completeness_validation.py
"""Validate the completeness metric against human labels derived from the trait-level
calibration CSVs. The GT is derived from free-text notes via an auditable keyword table;
ambiguous outputs are dropped (counted), never coerced to complete."""

from __future__ import annotations

from app.calibration import cohens_kappa

# Order matters: the FIRST matching bucket wins (worst incompleteness first).
_KEYWORDS = [
    ("fragment", ["not a plant", "junk", "garbage", "blob", "unrecognizable", "wtf"]),
    (
        "isolated-organ",
        [
            "only a fruit",
            "just a fruit",
            "isolated",
            "single organ",
            "only a leaf",
            "detached",
            "lone ",
        ],
    ),
    ("partial-organism", ["partial", "incomplete", "missing", "fragment of", "half a"]),
]
_COMPLETE_HINTS = ["whole", "complete", "full plant", "looks fine", "looks good", "ok", "correct"]


def map_note_to_category(human_verdict: str, note: str) -> str | None:
    text = f"{human_verdict} {note}".lower()
    if not text.strip():
        return None
    for cat, kws in _KEYWORDS:
        if any(k in text for k in kws):
            return cat
    if any(h in text for h in _COMPLETE_HINTS):
        return "complete"
    return None  # ambiguous -> drop from the eval set


_SEVERITY = {"fragment": 0, "isolated-organ": 1, "partial-organism": 2, "complete": 3}


def gt_by_output(rows: list[dict]) -> dict[int, str]:
    """Aggregate trait-level rows to one category per output_id: the WORST (lowest-severity)
    non-None mapped label across that output's rows. Outputs with only ambiguous rows drop."""
    worst: dict[int, str] = {}
    for r in rows:
        cat = map_note_to_category(r.get("human_verdict", ""), r.get("note", ""))
        if cat is None:
            continue
        oid = r["output_id"]
        if oid not in worst or _SEVERITY[cat] < _SEVERITY[worst[oid]]:
            worst[oid] = cat
    return worst


def _binary(cat: str) -> str:
    return "complete" if cat == "complete" else "incomplete"


def agreement(pred: dict[int, str], gt: dict[int, str]) -> dict:
    """κ (binary complete/incomplete + full 4-way) and isolated-organ recall over the
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
