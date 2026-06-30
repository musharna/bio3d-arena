"""Mode-C human-calibration CSV round-trip.

Two subcommands:

  export  Sample a stratified, BLIND set of (output, trait) verdicts to a CSV for a
          human to label. "Blind" = the VLM's verdict is NOT written to the CSV, so the
          labeller is not anchored to it (which would inflate kappa). Sampling balances
          across the VLM verdict categories per trait_class so kappa is not degenerate.

  ingest  Read the filled CSV (human_verdict column), feed the labels to
          service.recompute_trait_calibration (per-class Cohen's kappa, gate = k>=0.6 &
          n>=20), then service.recompute_trait_scores. DRY-RUN BY DEFAULT — previews
          per-class kappa without touching the DB; pass --commit to write.

The two pure seams (stratified_sample, parse_label_rows) are unit-tested; main() wires
the DB. Run against the study DB with the same env as the judge pass:

  BIO3D_DATABASE_URL=sqlite:///$(pwd)/data/study/arena-study.db \\
  BIO3D_DATA_DIR=$(pwd)/.claude/worktrees/bio3d-arena-mvp/data \\
  .venv/bin/python scripts/calibration_labels.py export --out labels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config, judge, service  # noqa: E402,F401
from app.calibration import cohens_kappa  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import TraitRubric, TraitVerdict  # noqa: E402

VOCAB = {"present_correct", "present_wrong", "absent", "not_assessable"}

CSV_FIELDS = [
    "output_id",
    "trait_key",
    "trait_class",
    "taxon",
    "expected",
    "visual",
    "contact_sheet",
    "human_verdict",
]

UNJUDGEABLE_EXPECTED = {"", "not explicitly stated", "unknown", "n/a", "na", "none"}


def is_judgeable(expected) -> bool:
    """A trait belongs in the calibration sample only if its `expected` value names
    something concrete to check the model against. Literature extraction sometimes emits a
    trait whose expected value is 'not explicitly stated' (the source didn't give one) —
    that trait has nothing to score, so it must not enter the sample or it pollutes kappa."""
    e = (expected or "").strip().lower()
    if e in UNJUDGEABLE_EXPECTED:
        return False
    return not (e.startswith("not explicitly") or "not stated" in e or "not specified" in e)


def stratified_sample(verdicts, *, per_class, classes, seed):
    """Pick up to `per_class` rows per trait_class, balanced across the VLM verdict
    categories (round-robin over buckets) so the human sees a spread, not just the
    dominant verdict. Deterministic for a given seed. `classes` (set|None) filters
    which trait_classes to include.

    `verdicts`: list of dicts each with at least trait_class + vlm_verdict (plus the
    carry-through CSV columns)."""
    rng = random.Random(seed)
    by_class: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for v in verdicts:
        cls = v["trait_class"]
        if classes is not None and cls not in classes:
            continue
        by_class[cls][v["vlm_verdict"]].append(v)

    picked: list[dict] = []
    for cls in sorted(by_class):
        buckets = list(by_class[cls].values())
        for b in buckets:
            rng.shuffle(b)
        # round-robin across verdict buckets until we hit per_class or run dry
        chosen: list[dict] = []
        cursor = 0
        while len(chosen) < per_class and any(buckets):
            bucket = buckets[cursor % len(buckets)]
            if bucket:
                chosen.append(bucket.pop())
            else:
                buckets = [b for b in buckets if b]
                cursor = -1  # reset; next +=1 → 0
            cursor += 1
        picked.extend(chosen)
    return picked


def parse_label_rows(rows):
    """rows: iterable of dicts (csv.DictReader). Return (labels, skipped) where labels is
    a list of (output_id:int, trait_key, trait_class, human_verdict). Blank human_verdict
    rows are skipped; an out-of-vocab verdict is a loud ValueError (a typo must not be
    silently dropped — it would corrupt the agreement count)."""
    labels = []
    skipped = 0
    for r in rows:
        raw = (r.get("human_verdict") or "").strip().lower()
        if not raw:
            skipped += 1
            continue
        if raw not in VOCAB:
            raise ValueError(
                f"invalid human_verdict {raw!r} for output {r.get('output_id')}/"
                f"{r.get('trait_key')}; must be one of {sorted(VOCAB)}"
            )
        labels.append((int(r["output_id"]), r["trait_key"], r["trait_class"], raw))
    return labels, skipped


def _verdict_rows(db):
    """Join every stored TraitVerdict to its rubric's expected/visual/taxon for that
    trait_key, plus the on-disk contact-sheet path. Returns export-ready dicts."""
    rubrics = {r.id: r for r in db.execute(select(TraitRubric)).scalars()}
    trait_meta: dict[int, dict[str, dict]] = {}
    taxon_of: dict[int, str] = {}
    for rid, r in rubrics.items():
        taxon_of[rid] = r.taxon
        trait_meta[rid] = {t["key"]: t for t in json.loads(r.traits_json)}

    rows = []
    for v in db.execute(select(TraitVerdict)).scalars():
        meta = trait_meta.get(v.rubric_id, {}).get(v.trait_key, {})
        sheet = Path(config.ASSET_DIR) / "renders" / f"{v.output_id}_multi4.png"
        rows.append(
            {
                "output_id": v.output_id,
                "trait_key": v.trait_key,
                "trait_class": v.trait_class,
                "taxon": taxon_of.get(v.rubric_id, ""),
                "expected": meta.get("expected", ""),
                "visual": meta.get("visual", ""),
                "contact_sheet": str(sheet),
                "vlm_verdict": v.verdict,
            }
        )
    return rows


def cmd_export(args):
    db = SessionLocal()
    try:
        all_rows = _verdict_rows(db)
    finally:
        db.close()
    classes = set(args.classes.split(",")) if args.classes else None
    judgeable = [r for r in all_rows if is_judgeable(r["expected"])]
    dropped = len(all_rows) - len(judgeable)
    sample = stratified_sample(judgeable, per_class=args.per_class, classes=classes, seed=args.seed)
    missing = sum(1 for r in sample if not Path(r["contact_sheet"]).exists())
    out = Path(args.out)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in sample:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})  # human_verdict left blank
    per_cls = defaultdict(int)
    for r in sample:
        per_cls[r["trait_class"]] += 1
    print(f"wrote {len(sample)} blind rows → {out}")
    if dropped:
        print(f"  (dropped {dropped} unjudgeable-'expected' verdicts before sampling)")
    print("  per class: " + ", ".join(f"{c}={per_cls[c]}" for c in sorted(per_cls)))
    if missing:
        print(
            f"  WARNING: {missing}/{len(sample)} contact sheets not found at the listed "
            f"paths — check BIO3D_DATA_DIR (sheets live in the worktree data dir)."
        )
    print(
        "  Fill the 'human_verdict' column with one of: "
        + ", ".join(sorted(VOCAB))
        + " (open the contact_sheet image to judge), then run `ingest`."
    )


def cmd_ingest(args):
    with Path(args.csv).open(newline="") as f:
        rows = list(csv.DictReader(f))
    labels, skipped = parse_label_rows(rows)
    if not labels:
        print(f"no filled rows in {args.csv} ({skipped} blank) — nothing to do.")
        return
    print(f"parsed {len(labels)} labels ({skipped} blank rows skipped)")

    db = SessionLocal()
    try:
        # Always preview per-class kappa in-memory (matches the gate logic) before any write.
        stored = {
            (v.output_id, v.trait_key): v.verdict
            for v in db.execute(select(TraitVerdict)).scalars()
            if v.judge_model == judge.JUDGE_MODEL
        }
        by_class: dict[str, tuple[list, list]] = {}
        unmatched = 0
        for oid, key, cls, human in labels:
            vlm = stored.get((oid, key))
            if vlm is None:
                unmatched += 1
                continue
            h, m = by_class.setdefault(cls, ([], []))
            h.append(human)
            m.append(vlm)
        print(
            f"\nper-class agreement (gate: kappa>={service.MODE_C_KAPPA_BAR} & n>="
            f"{service.MODE_C_MIN_N}):"
        )
        print(f"  {'class':14s} {'n':>4s} {'kappa':>7s}  accepted")
        for cls in sorted(by_class):
            h, m = by_class[cls]
            k = cohens_kappa(h, m)
            n = len(h)
            ok = k is not None and k >= service.MODE_C_KAPPA_BAR and n >= service.MODE_C_MIN_N
            ks = "n/a" if k is None else f"{k:.3f}"
            print(f"  {cls:14s} {n:4d} {ks:>7s}  {'YES' if ok else 'no'}")
        if unmatched:
            print(f"  ({unmatched} labels had no matching stored verdict — ignored)")

        if not args.commit:
            print(
                "\nDRY RUN — no DB writes. Re-run with --commit to persist calibration "
                "+ recompute scores."
            )
            return

        cal = service.recompute_trait_calibration(db, labels)
        sc = service.recompute_trait_scores(db)
        accepted = sorted(service.accepted_trait_classes(db))
        print(
            f"\nCOMMITTED: calibrated {cal['classes']} classes, rescored {sc['outputs']} outputs."
        )
        print(f"  accepted classes: {accepted or '(none cleared the gate)'}")
    finally:
        db.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="write a blind labeling CSV")
    pe.add_argument("--out", default="calibration_labels.csv")
    pe.add_argument(
        "--per-class",
        type=int,
        default=25,
        help="rows per trait_class (>=20 needed to clear the n-gate; default 25)",
    )
    pe.add_argument(
        "--classes", default=None, help="comma-separated trait_class filter (default: all)"
    )
    pe.add_argument("--seed", type=int, default=20260630)
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("ingest", help="parse a filled CSV and (with --commit) calibrate")
    pi.add_argument("csv")
    pi.add_argument(
        "--commit",
        action="store_true",
        help="write TraitCalibration + recompute TraitScore (default: dry run)",
    )
    pi.set_defaults(func=cmd_ingest)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
