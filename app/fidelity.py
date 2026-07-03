"""Cross-paradigm biological-fidelity scorecard (read-only aggregation).

Joins three per-output signals — completeness (primary, validated κ=0.64), geometry F-score
(secondary, SP4-weak proxy), trait botanical-accuracy (experimental) — into a per-taxon
x paradigm scorecard. Writes nothing. capture_scan is a GT reference, not a competitor.
The board is the objective complement to the within-paradigm preference arena; fidelity vs
the same per-taxon GT is commensurable across paradigms, unlike preference votes."""

from __future__ import annotations

from collections import defaultdict

from app import paradigms, service
from app.models import Generator, Metric, TraitScore

AXES_META = [
    {
        "key": "completeness",
        "label": "Completeness",
        "badge": "validated (binary κ=0.64)",
        "primary": True,
    },
    {
        "key": "geometry",
        "label": "Geometry (F-score)",
        "badge": "geometric proxy — weak (SP4)",
        "primary": False,
    },
    {
        "key": "trait",
        "label": "Trait fidelity",
        "badge": "experimental (κ-negative)",
        "primary": False,
    },
]


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def fidelity_scorecard(db) -> dict:
    """Per-taxon x paradigm fidelity scorecard. Read-only; aggregates existing scores."""
    comp = service.completeness_rows(db)  # [{output_id, taxon, generator_id, category, score}]
    if not comp:
        return {"axes_meta": AXES_META, "taxa": []}

    para = {g.id: g.paradigm for g in db.query(Generator).all()}
    names = service.generator_display_names(db)
    fscore = {m.output_id: m.fscore for m in db.query(Metric).all()}
    trait = {t.output_id: t.botanical_accuracy for t in db.query(TraitScore).all()}

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in comp:
        if r["taxon"] is None or r["generator_id"] is None:
            continue
        p = para.get(r["generator_id"])
        if not p:
            continue  # skip un-backfilled generators (paradigm defaults to "", non-nullable)
        groups[(r["taxon"], p)].append(r)

    by_taxon: dict[str, dict] = defaultdict(lambda: {"rows": [], "reference": []})
    for (taxon, p), rows in groups.items():
        oids = [r["output_id"] for r in rows]
        scores = [r["score"] for r in rows]
        n_complete = sum(1 for r in rows if r["category"] == "complete")
        # Count only non-null values: a metric/trait row can exist with a NULL value
        # (unscored/failed read); `n` must reflect real coverage so n>0 ⟺ mean is real.
        geom = [fscore[o] for o in oids if fscore.get(o) is not None]
        tr = [trait[o] for o in oids if trait.get(o) is not None]
        best = max(rows, key=lambda r: r["score"] if r["score"] is not None else -1.0)
        row = {
            "paradigm": p,
            "paradigm_label": paradigms.DISPLAY_NAMES.get(p, p),
            "n": len(rows),
            "completeness": {
                "pct_complete": n_complete / len(rows),
                "mean_score": _mean(scores),
                "n": len(rows),
            },
            "geometry": {"mean_fscore": _mean(geom), "n": len(geom)},
            "trait": {"mean_accuracy": _mean(tr), "n": len(tr)},
            "best_model": {
                "name": names.get(best["generator_id"], str(best["generator_id"])),
                "score": best["score"],
            },
        }
        bucket = "reference" if p == "capture_scan" else "rows"
        by_taxon[taxon][bucket].append(row)

    taxa = []
    for taxon in sorted(by_taxon):
        block = by_taxon[taxon]
        block["rows"].sort(
            key=lambda r: (
                r["completeness"]["pct_complete"],
                r["completeness"]["mean_score"] or 0.0,
            ),
            reverse=True,
        )
        taxa.append({"taxon": taxon, "rows": block["rows"], "reference": block["reference"]})
    return {"axes_meta": AXES_META, "taxa": taxa}
