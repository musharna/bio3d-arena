"""Rubric authoring: build per-taxon botanical-trait rubrics with mandatory provenance.

Each rubric is a list of traits; every trait carries source_tier ("db"|"llm") + a non-empty
citation (Global Constraints). A structured-DB backbone (fetch_db_traits) and an LLM enrichment
pass (draft_llm_traits) are INJECTED functions so the unit test drives them with stubs; the real
implementations live behind --live (Anthropic client / external DB endpoints). validate_trait +
upsert_rubric are pure and network-free. Uses the same sys.path bootstrap as scripts/judge_vlm.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RECON_TAXA = {  # taxon -> task_id placeholder; resolve real ids in main() via Task lookup
    "Solanum lycopersicum": None,
    "Zea mays": None,
    "Pinus sylvestris": None,
    "Rosa": None,
    "Glycine max": None,
    "Arabidopsis thaliana": None,
}


def validate_trait(t: dict) -> None:
    """Raise ValueError if a trait dict is missing a required field, has a non-scoreable
    trait_class, an empty citation, or a source_tier outside {db, llm}."""
    from app.traits import SCORED_CLASSES

    required = ("key", "trait_class", "type", "expected", "visual", "source_tier", "citation")
    for f in required:
        if f not in t:
            raise ValueError(f"trait missing field {f!r}: {t}")
    if t["trait_class"] not in SCORED_CLASSES:
        raise ValueError(f"trait_class {t['trait_class']!r} not scoreable")
    if t["source_tier"] not in ("db", "llm"):
        raise ValueError(f"bad source_tier {t['source_tier']!r}")
    if not str(t.get("citation", "")).strip():
        raise ValueError("trait has empty citation")


def upsert_rubric(db, taxon, task_id, traits):
    """Validate every trait, then insert-or-update the TraitRubric row for taxon."""
    from app.models import TraitRubric

    for t in traits:
        validate_trait(t)
    row = db.query(TraitRubric).filter_by(taxon=taxon).first() or TraitRubric(taxon=taxon)
    row.task_id = task_id
    row.traits_json = json.dumps(traits)
    db.add(row)
    db.commit()
    return row


# --- Pluggable trait sources (stubs by default; real impls wired behind --live) ----------------


def fetch_db_traits(taxon: str) -> list[dict]:
    """Structured-DB backbone (POWO / Wikidata / TRY). Network-bound — only reachable via --live.

    Kept isolated so the rest of the module is testable without network. The real endpoints are
    an implementation-time decision; until wired, fail loud rather than silently returning [].."""
    raise NotImplementedError(
        "fetch_db_traits requires the structured-DB integration (POWO/Wikidata/TRY); "
        "run with --live once endpoints are wired."
    )


def draft_llm_traits(taxon: str) -> list[dict]:
    """LLM enrichment pass. Network-bound (Anthropic client) — only reachable via --live."""
    raise NotImplementedError(
        "draft_llm_traits requires the Anthropic client; run with --live once enabled."
    )


def build_rubric_traits(
    taxon: str,
    *,
    fetch_db=fetch_db_traits,
    draft_llm=draft_llm_traits,
) -> list[dict]:
    """Assemble + validate one taxon's traits from the injected db backbone + llm enrichment.

    Each source must stamp source_tier + citation on its traits; we re-validate here so a buggy
    source can't smuggle an uncited trait through."""
    traits: list[dict] = []
    for t in fetch_db(taxon):
        t.setdefault("source_tier", "db")
        traits.append(t)
    for t in draft_llm(taxon):
        t.setdefault("source_tier", "llm")
        traits.append(t)
    for t in traits:
        validate_trait(t)
    return traits


def _resolve_task_ids(db, taxa: dict) -> dict:
    """Best-effort: map each taxon to a Task id whose title/prompt mentions it (None if absent)."""
    from app.models import Task

    resolved = dict(taxa)
    for taxon in resolved:
        if resolved[taxon] is not None:
            continue
        row = (
            db.query(Task)
            .filter((Task.title.ilike(f"%{taxon}%")) | (Task.prompt.ilike(f"%{taxon}%")))
            .first()
        )
        resolved[taxon] = row.id if row else None
    return resolved


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--taxa",
        default=",".join(RECON_TAXA),
        help="comma-separated taxa (default: the 6 recon species)",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="use the real structured-DB + Anthropic enrichment sources (network)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="build + validate rubrics but write nothing to the DB",
    )
    args = ap.parse_args()

    if not args.live:
        print(
            "refusing to build real rubrics without --live "
            "(fetch_db_traits/draft_llm_traits are network-bound stubs).",
            file=sys.stderr,
        )
        return 2

    from app.database import SessionLocal

    taxa = {t.strip(): None for t in args.taxa.split(",") if t.strip()}
    with SessionLocal() as db:
        taxa = _resolve_task_ids(db, taxa)
        written = 0
        for taxon, task_id in taxa.items():
            traits = build_rubric_traits(taxon)
            if args.dry_run:
                print(f"[dry-run] {taxon} (task={task_id}): {len(traits)} traits")
                continue
            upsert_rubric(db, taxon, task_id, traits)
            written += 1
            print(f"wrote rubric for {taxon} (task={task_id}): {len(traits)} traits")
    print({"rubrics": written, "dry_run": args.dry_run})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
