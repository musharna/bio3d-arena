#!/usr/bin/env python3
"""Re-derive stored Completeness categories from their persisted checklists.

`app.completeness.derive` is pure over (inventory, organs_present) — so when the derivation
logic changes, every stored row can be recomputed from its own `checklist_json` with NO
re-render and NO VLM call. This script does exactly that: for each completeness row it loads
`organs_present` from the checklist, looks up the taxon inventory (via the output's TraitRubric),
re-derives (category, score), and rewrites the row iff the category changed. Idempotent.

Motivating change: dropping the noisy complement→`malformed` gate (see
memory/animal_fidelity_firming_2026-07-17.md) reclassifies correctly-built-but-VLM-miscounted
animals from `malformed` to `complete`. Score is unaffected (it was always part-type coverage).

Usage:
    python -m scripts.rederive_completeness --db data/study/arena-study.db            # dry-run
    python -m scripts.rederive_completeness --db data/study/arena-study.db --apply     # write
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter

from app.completeness import derive
from app.organ_inventory import inventory_for


def _rederive(db_path: str, *, apply: bool, only_old: set[str] | None = None) -> int:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT co.id AS cid, co.category AS old_cat, co.score AS old_score,
               co.checklist_json AS cj, tr.taxon AS taxon
        FROM completeness co
        JOIN model_output mo ON co.output_id = mo.id
        JOIN trait_rubric tr ON tr.task_id = mo.task_id
        """
    ).fetchall()

    flips: Counter = Counter()
    by_taxon: Counter = Counter()
    no_inventory = 0
    bad_checklist = 0
    changed = []
    for r in rows:
        if only_old is not None and r["old_cat"] not in only_old:
            continue
        inv = inventory_for(r["taxon"])
        if inv is None:
            no_inventory += 1
            continue
        try:
            organs_present = (json.loads(r["cj"]) or {}).get("organs_present") or []
        except (json.JSONDecodeError, TypeError):
            bad_checklist += 1
            continue
        new_cat, new_score = derive(inv, organs_present)
        if new_cat != r["old_cat"]:
            flips[f"{r['old_cat']} -> {new_cat}"] += 1
            by_taxon[r["taxon"]] += 1
            changed.append((r["cid"], new_cat, new_score))

    print(f"db: {db_path}")
    print(
        f"  rows scanned: {len(rows)}  no-inventory: {no_inventory}  bad-checklist: {bad_checklist}"
    )
    print(f"  category flips: {sum(flips.values())}")
    for k, n in flips.most_common():
        print(f"    {k}: {n}")
    if by_taxon:
        print("  by taxon: " + ", ".join(f"{t}×{n}" for t, n in by_taxon.most_common()))

    if not apply:
        print("  DRY-RUN — no writes. Re-run with --apply to persist.")
        con.close()
        return 0

    for cid, new_cat, new_score in changed:
        con.execute(
            "UPDATE completeness SET category = ?, score = ? WHERE id = ?",
            (new_cat, new_score, cid),
        )
    con.commit()
    con.close()
    print(f"  APPLIED {len(changed)} updates.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="path to the SQLite DB to re-derive")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument(
        "--only-old",
        default=None,
        help="comma-separated list of current categories to re-derive (default: all). Scope a "
        "targeted correction, e.g. --only-old malformed, without touching unrelated drift.",
    )
    args = ap.parse_args()
    only_old = {c.strip() for c in args.only_old.split(",") if c.strip()} if args.only_old else None
    return _rederive(args.db, apply=args.apply, only_old=only_old)


if __name__ == "__main__":
    sys.exit(main())
