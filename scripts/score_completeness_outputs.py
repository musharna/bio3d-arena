#!/usr/bin/env python3
"""Score organism-level completeness for an EXPLICIT list of output ids.

`score_completeness.py` scores every eligible output of a task (no skip), which
re-renders and re-VLMs a whole task even when only a few outputs need it. This
helper scores exactly the ids you name — used to (a) score a handful of newly
generated outputs, or (b) refresh outputs whose cached contact sheet is stale
(pass --invalidate-sheets to delete the cached turntable sheet so it re-renders
from the current GLB before scoring).

Reuses score_completeness's providers (same CONDITION, SCORER_VERSION, client,
browser capture) so the verdicts are directly comparable. Requires
ANTHROPIC_API_KEY. Snapshot the DB before pointing this at study.

    BIO3D_DATABASE_URL=sqlite:////abs/data/study/arena-study.db \\
    BIO3D_DATA_DIR=/abs/data \\
    python -u scripts/score_completeness_outputs.py --outputs 1062,1063,1064,1065
    ... --outputs <34 ids> --invalidate-sheets      # refresh stale renders
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

import scripts.score_completeness as sc  # noqa: E402
from app import config  # noqa: E402
from app.completeness import score_outputs  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.judge_render import contact_sheet_path  # noqa: E402


def build_work(db, output_ids: list[int]) -> list[dict]:
    """One {output_id, taxon} row per id, taxon resolved via the output's task TraitRubric."""
    work = []
    for oid in output_ids:
        taxon = db.execute(
            text(
                "SELECT tr.taxon FROM model_output mo "
                "JOIN trait_rubric tr ON tr.task_id = mo.task_id WHERE mo.id = :o"
            ),
            {"o": oid},
        ).scalar()
        if taxon is None:
            print(f"  WARN output {oid}: no TraitRubric taxon — skipping")
            continue
        work.append({"output_id": oid, "taxon": taxon})
    return work


def _already_scored(db, scorer_version: str) -> set[int]:
    from app.models import Completeness

    return {
        c.output_id
        for c in db.query(Completeness.output_id).filter(
            Completeness.scorer_version == scorer_version
        )
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--outputs", help="comma-separated output ids")
    src.add_argument("--outputs-file", help="file of comma/newline-separated output ids")
    ap.add_argument(
        "--invalidate-sheets",
        action="store_true",
        help="delete each output's cached turntable contact sheet first (force re-render)",
    )
    ap.add_argument(
        "--skip-scored",
        action="store_true",
        help="skip ids already scored at the current scorer version — makes a run resumable",
    )
    args = ap.parse_args(argv)
    raw = args.outputs if args.outputs else Path(args.outputs_file).read_text()
    output_ids = [int(x) for x in raw.replace("\n", ",").split(",") if x.strip()]

    init_db()
    with SessionLocal() as db:
        if args.skip_scored:
            done = _already_scored(db, sc.SCORER_VERSION)
            before = len(output_ids)
            output_ids = [o for o in output_ids if o not in done]
            print(
                f"skip-scored: {before - len(output_ids)} already done, {len(output_ids)} to do",
                flush=True,
            )

        work = build_work(db, output_ids)
        if args.invalidate_sheets:
            n = 0
            for item in work:
                p = os.path.join(
                    config.ASSET_DIR, contact_sheet_path(item["output_id"], sc.CONDITION)
                )
                if os.path.exists(p):
                    os.remove(p)
                    n += 1
            print(f"invalidated {n} cached contact sheets", flush=True)

        sheet_for = sc._sheet_provider(db, sc._capture_multi())
        client = sc._build_client()
        totals = {"scored": 0, "skipped_no_inventory": 0, "errors": 0}
        # Commit after EACH output so a long run is durable and --skip-scored can resume it.
        for i, item in enumerate(work, 1):
            s = score_outputs(
                db, [item], client=client, sheet_for=sheet_for, scorer_version=sc.SCORER_VERSION
            )
            db.commit()
            for k in totals:
                totals[k] += s.get(k, 0)
            print(
                f"  [{i}/{len(work)}] output {item['output_id']} ({item['taxon']}): "
                f"scored={s['scored']} err={s['errors']}",
                flush=True,
            )
    print(totals, flush=True)
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
