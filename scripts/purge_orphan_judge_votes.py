#!/usr/bin/env python3
"""Delete JudgeVote rows whose referenced ModelOutput no longer exists.

Why these exist
---------------
SQLite does not enforce foreign keys unless ``PRAGMA foreign_keys=ON`` is set per
connection, and this app's connect listener sets journal_mode/synchronous/busy_timeout
but not that (``app/database.py``). The FKs are therefore DECLARED BUT NOT ENFORCED, so
removing the 12 cube-only agentic outputs (task #90) silently orphaned the judge votes
that referenced them — 47 rows, found by the 2026-07-26 pre-launch audit via
``PRAGMA foreign_key_check``.

Scope of this script — remediation, NOT the cause
-------------------------------------------------
This deletes the orphans. It does NOT stop new ones appearing: any future delete of a
parent row can orphan children again while the pragma is off. Turning enforcement on is
the causal fix, but it is a separate decision with real blast radius — ``conftest.py``
already reports an unresolvable circular FK between ``model_output`` and ``task``, which
enforcement would turn from a warning into a failure.

Impact of leaving them
----------------------
Latent, not live: every ``/models/<slug>`` page and every board renders 200 with the
orphans present. But they are the same class as the dangling ``calibration_pair`` rows
that WERE a latent 500 in the 2026-06-29 audit, and they silently drop 47 judge votes
from the Bradley-Terry fit.

Usage
-----
    python scripts/purge_orphan_judge_votes.py             # dry run
    python scripts/purge_orphan_judge_votes.py --apply     # delete

Snapshot the DB first when pointing this at study.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import JudgeVote, ModelOutput  # noqa: E402


def orphan_judge_vote_ids(db: Session) -> list[int]:
    """Ids of JudgeVote rows referencing an output id that is absent from model_output.

    Checks BOTH sides of the comparison — a vote is orphaned if either side is gone.
    """
    live = {oid for (oid,) in db.execute(select(ModelOutput.id))}
    return [
        jv_id
        for jv_id, a, b in db.execute(
            select(JudgeVote.id, JudgeVote.output_a_id, JudgeVote.output_b_id)
        )
        if a not in live or b not in live
    ]


def purge(db: Session, *, apply: bool) -> dict:
    ids = orphan_judge_vote_ids(db)
    if apply and ids:
        for jv in db.execute(select(JudgeVote).where(JudgeVote.id.in_(ids))).scalars():
            db.delete(jv)
        db.commit()
    return {"orphans": len(ids), "deleted": len(ids) if apply else 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    args = ap.parse_args(argv)

    init_db()
    with SessionLocal() as db:
        before = len(list(db.execute(select(JudgeVote.id))))
        res = purge(db, apply=args.apply)
        after = len(list(db.execute(select(JudgeVote.id))))

    print(f"judge_vote rows: {before} -> {after}")
    print(f"  orphaned : {res['orphans']}")
    print(f"  deleted  : {res['deleted']}")
    print("APPLIED" if args.apply else "DRY RUN (pass --apply to delete)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
