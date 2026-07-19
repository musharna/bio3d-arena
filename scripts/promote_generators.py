# scripts/promote_generators.py
"""Promote named generators — their outputs and the evidence describing those outputs — from one
arena DB into another (e.g. the preview DB into the study DB).

Why this exists: the previous promote pattern was a whole-DB file copy ("checkpoint WAL, snapshot,
cp"). That is only safe when the source is a pristine descendant of the target. It stopped being
safe the moment the preview DB accumulated local UI-test votes: copying the file would have
injected 124 synthetic votes (dated after the study DB's last real vote) into the canonical
ranking. This script moves outputs WITHOUT moving anything vote-derived.

Three rules keep it honest:
  1. VOTE_DERIVED tables never cross. Ranking evidence is earned in the target, not imported.
  2. `n_comparisons` is RESET to 0. It is a cached vote counter; the votes behind it stay in the
     source, so carrying the number over would assert comparisons the target has no record of.
  3. A table with an output_id that is in neither allow-list fails the run loudly. A new table
     must be classified deliberately — never silently copied, never silently dropped.

Assets are NOT copied: config.ASSET_DIR derives from BIO3D_DATA_DIR and is independent of the DB,
so both DBs already address the same files on disk.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.paradigms import classify_paradigm  # noqa: E402  (shared canonical source→paradigm rules)


class PromoteError(RuntimeError):
    """A promote that would corrupt the target. Always fatal — never downgraded to a warning."""


# Evidence ABOUT an output: computed from the asset itself, carries no vote signal. Safe to move.
EVIDENCE_TABLES = (
    "admissibility",
    "metric",
    "completeness",
    "trait_score",
    "organ_metric",
    "trait_verdict",
    "model_scope",
    # The agentic paradigm's render->critique->revise trail: render path, the model's own critique
    # note, perceptual distances. It records how the output was MADE, not how it was voted on —
    # no session, no ballot. (Rule 3 caught this table as unclassified on the first agentic
    # promote and aborted the run, which is exactly what it is there for.)
    "critique",
)

# Evidence keyed by GENERATOR rather than by output: the commissioned-generation attempt log.
# /procedural computes pass@1 from these rows, and a FAILED attempt has output_id IS NULL — so
# copying this table keyed by output would carry only the successes and silently report pass@1 as
# 100%. It travels with the generator, failures included.
GENERATOR_EVIDENCE_TABLES = ("commission_attempt",)

# Vote-, session-, or curation-derived. NEVER promoted: these are earned in the target DB.
VOTE_DERIVED_TABLES = (
    "vote",
    "comparison",
    "judge_vote",
    "kballot",
    "voter_session",
    "output_flag",
    "gold_pair",
    "calibration_pair",
    "submission",
)


def _cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _tables_with_output_fk(con: sqlite3.Connection) -> list[str]:
    out = []
    for (name,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        if any(fk[2] == "model_output" for fk in con.execute(f"PRAGMA foreign_key_list({name})")):
            out.append(name)
    return out


def promote(source: str, target: str, slugs: list[str], *, apply: bool = False) -> dict:
    """Copy each named generator, its non-gold+gold outputs, and their evidence rows from `source`
    into `target`. Returns a per-table count of rows written (or, when apply=False, of rows that
    WOULD be written). Raises PromoteError rather than writing a corrupt target."""
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    src.row_factory = sqlite3.Row
    try:
        # --- resolve the generators in the source
        qs = ",".join("?" * len(slugs))
        gens = src.execute(f"SELECT * FROM generator WHERE slug IN ({qs})", slugs).fetchall()
        found = {g["slug"] for g in gens}
        if missing := set(slugs) - found:
            raise PromoteError(f"generator(s) not found in source: {sorted(missing)}")

        gen_ids = [g["id"] for g in gens]
        gq = ",".join("?" * len(gen_ids))
        outs = src.execute(
            f"SELECT * FROM model_output WHERE generator_id IN ({gq})", gen_ids
        ).fetchall()
        out_ids = [o["id"] for o in outs]

        # --- already promoted? (idempotent re-run)
        have_gen = {
            r[0] for r in dst.execute(f"SELECT slug FROM generator WHERE slug IN ({qs})", slugs)
        }
        have_out = set()
        if out_ids:
            oq = ",".join("?" * len(out_ids))
            have_out = {
                r[0]
                for r in dst.execute(f"SELECT id FROM model_output WHERE id IN ({oq})", out_ids)
            }

        # --- an id already used by a DIFFERENT row in the target is a collision, not a re-run
        for o in outs:
            if o["id"] in have_out:
                tgt = dst.execute(
                    "SELECT generator_id, asset_path FROM model_output WHERE id=?", (o["id"],)
                ).fetchone()
                src_gen_slug = next(g["slug"] for g in gens if g["id"] == o["generator_id"])
                tgt_gen_slug = dst.execute(
                    "SELECT slug FROM generator WHERE id=?", (tgt[0],)
                ).fetchone()
                if tgt_gen_slug is None or tgt_gen_slug[0] != src_gen_slug:
                    raise PromoteError(
                        f"id collision: target model_output {o['id']} already belongs to a "
                        f"different generator ({tgt_gen_slug and tgt_gen_slug[0]!r}, "
                        f"asset {tgt[1]!r}), not {src_gen_slug!r}"
                    )

        # --- every output's task must already exist in the target (we never invent tasks)
        task_ids = {o["task_id"] for o in outs}
        if task_ids:
            tq = ",".join("?" * len(task_ids))
            present = {
                r[0] for r in dst.execute(f"SELECT id FROM task WHERE id IN ({tq})", list(task_ids))
            }
            if orphan := task_ids - present:
                raise PromoteError(
                    f"target is missing task(s) {sorted(orphan)} referenced by these outputs — "
                    "promote the tasks first"
                )

        # --- fail loud on any output-referencing table we have not classified
        if out_ids:
            unclassified = [
                t
                for t in _tables_with_output_fk(src)
                if t not in EVIDENCE_TABLES
                and t not in VOTE_DERIVED_TABLES
                and t not in GENERATOR_EVIDENCE_TABLES
                and t != "task"
            ]
            for t in unclassified:
                oq = ",".join("?" * len(out_ids))
                n = src.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE output_id IN ({oq})", out_ids
                ).fetchone()[0]
                if n:
                    raise PromoteError(
                        f"table {t!r} has {n} row(s) for the promoted outputs but is in neither "
                        "EVIDENCE_TABLES nor VOTE_DERIVED_TABLES — classify it explicitly"
                    )

        summary: dict[str, int] = {"generators": 0, "model_output": 0}

        new_gens = [g for g in gens if g["slug"] not in have_gen]
        new_outs = [o for o in outs if o["id"] not in have_out]
        summary["generators"] = len(new_gens)
        summary["model_output"] = len(new_outs)

        # Auto-heal a blank paradigm at the study boundary. A just-generated api:/api:text: generator
        # is born blank (ingest doesn't tag it; a separate backfill pass does) and would land
        # INVISIBLE on its board, which filters paradigm == X. Classify it from its output sources
        # via the canonical classifier and carry the result to the target; fail loud if unmappable so
        # nothing lands unclassified. Computed before the apply gate so a dry-run surfaces it too.
        sources_by_gen: dict[int, set[str]] = {}
        for o in outs:
            sources_by_gen.setdefault(o["generator_id"], set()).add(o["source"])
        resolved_paradigm: dict[int, str] = {}
        for g in new_gens:
            p = g["paradigm"]
            if not p:
                p = classify_paradigm(g["slug"], g["kind"], sources_by_gen.get(g["id"], set()))
                if p is None:
                    raise PromoteError(
                        f"generator {g['slug']!r} has a blank paradigm and no classify rule matches "
                        f"its sources {sorted(sources_by_gen.get(g['id'], set()))} — tag it (or add a "
                        "rule) before promoting; a blank-paradigm generator is invisible on its board"
                    )
            resolved_paradigm[g["id"]] = p

        promoted_ids = [o["id"] for o in new_outs]
        for t in EVIDENCE_TABLES:
            if t not in {r[0] for r in src.execute("SELECT name FROM sqlite_master")}:
                continue
            if not promoted_ids:
                summary[t] = 0
                continue
            oq = ",".join("?" * len(promoted_ids))
            summary[t] = src.execute(
                f"SELECT COUNT(*) FROM {t} WHERE output_id IN ({oq})", promoted_ids
            ).fetchone()[0]

        for t in GENERATOR_EVIDENCE_TABLES:
            if t not in {r[0] for r in src.execute("SELECT name FROM sqlite_master")}:
                continue
            have_ids = {r[0] for r in dst.execute(f"SELECT id FROM {t}")}
            summary[t] = sum(
                1
                for r in src.execute(f"SELECT id FROM {t} WHERE generator_id IN ({gq})", gen_ids)
                if r[0] not in have_ids
            )

        if not apply:
            return summary

        # ---- write
        for g in new_gens:
            cols = [c for c in g.keys() if c in _cols(dst, "generator")]
            vals = [resolved_paradigm[g["id"]] if c == "paradigm" else g[c] for c in cols]
            dst.execute(
                f"INSERT INTO generator ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                vals,
            )
        for o in new_outs:
            cols = [c for c in o.keys() if c in _cols(dst, "model_output")]
            vals = [0 if c == "n_comparisons" else o[c] for c in cols]  # rule 2: reset the counter
            dst.execute(
                f"INSERT INTO model_output ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                vals,
            )
        for t in EVIDENCE_TABLES:
            if not summary.get(t):
                continue
            oq = ",".join("?" * len(promoted_ids))
            rows = src.execute(f"SELECT * FROM {t} WHERE output_id IN ({oq})", promoted_ids)
            dst_cols = _cols(dst, t)
            for r in rows:
                cols = [c for c in r.keys() if c in dst_cols and c != "id"]
                dst.execute(
                    f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [r[c] for c in cols],
                )

        # Generator-keyed evidence (the attempt log). Selected by generator_id — NOT by output —
        # so failed attempts (output_id IS NULL) come across too; dropping them would inflate
        # pass@1. Rows already in the target are skipped, keeping a re-run a no-op.
        for t in GENERATOR_EVIDENCE_TABLES:
            if t not in {r[0] for r in src.execute("SELECT name FROM sqlite_master")}:
                continue
            have_ids = {r[0] for r in dst.execute(f"SELECT id FROM {t}")}
            rows = src.execute(
                f"SELECT * FROM {t} WHERE generator_id IN ({gq})", gen_ids
            ).fetchall()
            dst_cols = _cols(dst, t)
            n = 0
            for r in rows:
                if r["id"] in have_ids:
                    continue
                cols = [c for c in r.keys() if c in dst_cols]
                dst.execute(
                    f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [r[c] for c in cols],
                )
                n += 1
            summary[t] = n
        dst.commit()
        return summary
    finally:
        src.close()
        dst.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True, help="source DB file (e.g. data/arena-preview.db)")
    ap.add_argument(
        "--target", required=True, help="target DB file (e.g. data/study/arena-study.db)"
    )
    ap.add_argument("--generator", action="append", required=True, metavar="SLUG")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = ap.parse_args()

    summary = promote(args.source, args.target, args.generator, apply=args.apply)
    verb = "promoted" if args.apply else "WOULD promote (dry-run)"
    print(f"{verb}: {summary}")
    print("vote-derived tables NOT copied:", ", ".join(VOTE_DERIVED_TABLES))
    print("n_comparisons reset to 0 on every promoted output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
