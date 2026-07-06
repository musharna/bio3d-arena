"""Feasibility probe: which mechanism (generic-CLIP zero-shot, BioCLIP, completeness-VLM) best
detects each reference-image defect (fruit_only, wrong_species, poor_exemplar) — across
IN-DOMAIN real photos and OUT-OF-DOMAIN 3D-render sheets.

Reads a hand-labeled manifest (docs/superpowers/probe_labels.json), runs the applicable
mechanisms per item, aggregates a per-(mechanism, defect) confusion matrix via the pure
`confusion()` function, writes a markdown + CSV report, and prints a decision table:

  - in-domain (photo) defects: which mechanism (clip / bioclip / vlm) wins per defect.
  - render species-separation GO/NO-GO for Component #3 (wrong_species-on-renders revival):
    read off the "wrong_species" row of the bioclip confusion matrix (see MECHANISM DESIGN
    below) — a strong precision+recall there is a GO for building the render-based
    wrong-species detector; weak/near-chance is a NO-GO (component stays dead, per the plan's
    Global Constraints / Task 5).

MECHANISM DESIGN (documented here since main() is not unit-tested — only confusion() is):

  photo-domain items run THREE mechanisms:
    - pred_clip:    generic OpenCLIP 2-way zero-shot, COMPOSITION framing ("whole organism" vs
                    "a close-up of only a single isolated fruit/organ") -> {"good","fruit_only"}.
                    Structurally cannot predict wrong_species/poor_exemplar (always FN there) —
                    that is itself the probe's point: composition-only signal is fruit_only-scoped.
    - pred_bioclip: BioCLIP `species_rep_score` (does this read as a clear, identifiable photo
                    of the CLAIMED species?), thresholded at `reference_qa.SPECIES_REP_MIN`.
                    Below threshold -> "poor_exemplar" (BioCLIP has one scalar signal — it cannot
                    itself distinguish "wrong species" from "genuinely bad photo"; both count as
                    poor_exemplar predictions, which the confusion matrix will expose as noisy
                    FP/FN on the OTHER photo defects if BioCLIP is in fact conflating them).
    - pred_vlm:     `reference_qa.assess_organ_coverage` (completeness-VLM reused with a
                    photo-framed prompt) -> "fruit_only" iff `fruit_only is True`, else "good".
                    `fruit_only is None` (single-required-organ body plans: fungi/gourd — see
                    app/reference_qa.py) is treated as "good" (undetermined defers to CLIP, per
                    reference_qa's documented behavior).

  render-domain items run ONE mechanism (pred_bioclip only — this domain-shift check is the
  entire point of the render arm):
    - `claimed` = item["shown_as"] if present else item["taxon"] (a "right_species" item has no
      shown_as: its claim IS the truth, so the test becomes a specificity/no-false-alarm check).
    - `species_rep_score(bioclip_bundle, png, common=<claimed's common name>, taxon=claimed)`,
      thresholded at SPECIES_REP_MIN -> "right_species" (render visually matches its claim) or
      "wrong_species" (render does NOT match its claim — a mismatch was detected).
    - This reuses the already-shipped, Task-1-tested `species_rep_score` directly rather than
      inventing a new true-vs-foil zero-shot pair.

No DB access (read-only over labeled asset files). Any Anthropic client is constructed lazily
inside main() (never at import time) so importing this module for `confusion()` stays cheap for
the unit test."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.organ_inventory import inventory_for  # noqa: E402
from app.storage import get_storage  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "superpowers"
LABELS_PATH = RESULTS_DIR / "probe_labels.json"

# Composition zero-shot labels (generic CLIP): whole-organism vs isolated-organ close-up.
_COMPOSITION_LABELS = {
    "good": "a clear photograph of the entire living organism, whole body visible",
    "fruit_only": (
        "a close-up photograph showing only a single detached fruit, pod, cap, or isolated "
        "reproductive organ, with no vegetative body visible"
    ),
}


def confusion(records: list[dict]) -> dict:
    """Pure aggregation: for each (mechanism, defect) pair, one-vs-rest TP/FP/TN/FN counts.

    `records` is a list of dicts each carrying a `"true"` label and zero or more `"pred_<mech>"`
    keys (a record missing a given mechanism's key is excluded from that mechanism's counts —
    this is how photo-only mechanisms and render-only mechanisms coexist in one dataset).
    `mechs` and `defects` are both derived from the data, not hardcoded, so this generalizes to
    however many mechanisms/labels a given probe run actually exercises. "good" is the sole
    non-defect sentinel (render domain's non-defect label is "right_species", which therefore
    appears as its own one-vs-rest column too — read its row directly for the GO/NO-GO call).
    """
    out = {}
    mechs = sorted({k[5:] for r in records for k in r if k.startswith("pred_")})
    defects = sorted({r["true"] for r in records if r["true"] != "good"})
    for m in mechs:
        out[m] = {}
        for d in defects:
            tp = fp = tn = fn = 0
            for r in records:
                pred = r.get(f"pred_{m}")
                if pred is None:
                    continue
                actual_pos, pred_pos = (r["true"] == d), (pred == d)
                tp += actual_pos and pred_pos
                fp += (not actual_pos) and pred_pos
                fn += actual_pos and (not pred_pos)
                tn += (not actual_pos) and (not pred_pos)
            out[m][d] = {"tp": tp, "fp": fp, "tn": tn, "fn": fn}
    return out


def _common_for(taxon: str, items: list[dict]) -> str:
    """Best-effort common name for a taxon that may only appear as a `shown_as` foil (no
    dedicated `common` field of its own) — reuse another labeled item's common name if one
    names the same taxon, else fall back to the SPECIES_COMMON registry, else the taxon itself."""
    for it in items:
        if it.get("taxon") == taxon and it.get("common"):
            return it["common"]
    try:
        from app.commission import SPECIES_COMMON

        if taxon in SPECIES_COMMON:
            return SPECIES_COMMON[taxon]
    except ImportError:
        pass
    return taxon


def _photo_preds(clip_bundle, bioclip_bundle, client, png: bytes, item: dict) -> dict:
    from app import species_id
    from app.reference_qa import SPECIES_REP_MIN, assess_organ_coverage

    preds = {}

    labels = list(_COMPOSITION_LABELS.values())
    key_by_text = {v: k for k, v in _COMPOSITION_LABELS.items()}
    probs = species_id.zero_shot(clip_bundle, png, labels)
    winner = max(probs, key=probs.get)
    preds["pred_clip"] = key_by_text[winner]

    score = species_id.species_rep_score(
        bioclip_bundle, png, common=item["common"], taxon=item["taxon"]
    )
    preds["pred_bioclip"] = "good" if score >= SPECIES_REP_MIN else "poor_exemplar"

    inv = inventory_for(item["taxon"])
    if inv is None:
        print(
            f"WARN: no organ_inventory for taxon={item['taxon']!r} ({item['path']}) — skipping vlm"
        )
    else:
        res = assess_organ_coverage(client, png, inventory=inv)
        preds["pred_vlm"] = "fruit_only" if res["fruit_only"] is True else "good"

    return preds


def _render_preds(bioclip_bundle, png: bytes, item: dict, all_items: list[dict]) -> dict:
    from app.reference_qa import SPECIES_REP_MIN
    from app.species_id import species_rep_score

    claimed = item.get("shown_as", item["taxon"])
    claimed_common = _common_for(claimed, all_items)
    score = species_rep_score(bioclip_bundle, png, common=claimed_common, taxon=claimed)
    pred = "right_species" if score >= SPECIES_REP_MIN else "wrong_species"
    return {"pred_bioclip": pred}


def _load_items() -> list[dict]:
    return json.loads(LABELS_PATH.read_text())


def _write_reports(records: list[dict], mat: dict, runid: str) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = RESULTS_DIR / f"probe_results_{runid}.md"
    csv_path = RESULTS_DIR / f"probe_results_{runid}.csv"

    lines = [
        f"# CLIP/BioCLIP feasibility probe — run `{runid}`",
        "",
        f"{len(records)} labeled items.",
        "",
    ]
    for mech in sorted(mat):
        lines.append(f"## Mechanism: {mech}")
        lines.append("")
        lines.append("| defect | tp | fp | tn | fn | precision | recall |")
        lines.append("|---|---|---|---|---|---|---|")
        for defect in sorted(mat[mech]):
            c = mat[mech][defect]
            prec = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else float("nan")
            rec = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else float("nan")
            lines.append(
                f"| {defect} | {c['tp']} | {c['fp']} | {c['tn']} | {c['fn']} | {prec:.2f} | {rec:.2f} |"
            )
        lines.append("")
    md_path.write_text("\n".join(lines))

    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mechanism", "defect", "tp", "fp", "tn", "fn"])
        for mech in sorted(mat):
            for defect in sorted(mat[mech]):
                c = mat[mech][defect]
                w.writerow([mech, defect, c["tp"], c["fp"], c["tn"], c["fn"]])

    return md_path, csv_path


def _print_decision_table(mat: dict) -> None:
    print("\n=== Decision table ===")
    photo_defects = {"fruit_only", "wrong_species", "poor_exemplar"} & {
        d for mech in mat for d in mat[mech]
    }
    for defect in sorted(photo_defects):
        best_mech, best_f1 = None, -1.0
        for mech in mat:
            c = mat[mech].get(defect)
            if c is None:
                continue
            prec = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
            rec = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
            if f1 > best_f1:
                best_mech, best_f1 = mech, f1
        print(f"  in-domain defect={defect!r}: chosen mechanism={best_mech!r} (f1={best_f1:.2f})")

    wrong = mat.get("bioclip", {}).get("wrong_species")
    if wrong is None:
        print("  render species-separation (#3): NO DATA (no wrong_species render items labeled)")
    else:
        prec = wrong["tp"] / (wrong["tp"] + wrong["fp"]) if (wrong["tp"] + wrong["fp"]) else 0.0
        rec = wrong["tp"] / (wrong["tp"] + wrong["fn"]) if (wrong["tp"] + wrong["fn"]) else 0.0
        go = prec >= 0.7 and rec >= 0.7
        print(
            f"  render species-separation (#3): precision={prec:.2f} recall={rec:.2f} -> "
            f"{'GO' if go else 'NO-GO'}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runid", default="latest", help="suffix for output report filenames")
    args = ap.parse_args()

    items = _load_items()
    store = get_storage()

    from app import species_id

    if not species_id.available():
        print("ERROR: open_clip not installed — cannot run the probe.", file=sys.stderr)
        return 1

    clip_bundle = species_id.load_model("clip")
    bioclip_bundle = species_id.load_model("bioclip")

    client = None
    if any(it["domain"] == "photo" for it in items):
        import os

        import anthropic

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    records = []
    for item in items:
        png = store.read(item["path"])
        rec = {"true": item["label"], "path": item["path"], "domain": item["domain"]}
        if item["domain"] == "photo":
            rec.update(_photo_preds(clip_bundle, bioclip_bundle, client, png, item))
        elif item["domain"] == "render":
            rec.update(_render_preds(bioclip_bundle, png, item, items))
        else:
            print(f"WARN: unknown domain {item['domain']!r} for {item['path']} — skipped")
            continue
        records.append(rec)

    mat = confusion(records)
    md_path, csv_path = _write_reports(records, mat, args.runid)
    print(f"Wrote {md_path}")
    print(f"Wrote {csv_path}")
    _print_decision_table(mat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
