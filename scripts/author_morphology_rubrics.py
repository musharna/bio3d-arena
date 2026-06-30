"""Load the authored morphology rubrics (app/trait_morphology.py) into the study DB,
replacing the literature-derived TraitRubric rows for the 6 study taxa.

--dry-run prints the per-taxon trait table + db/ref tier counts and does NOT write or hit
the network for verification. --commit assembles with the live Wikidata SPARQL backbone,
HTTP-resolve-verifies every citation (fail-loud on a dead link), and upserts the rubrics.
Snapshot the study DB before --commit (operator). Mirrors scripts/build_trait_rubrics.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.trait_morphology import MORPHOLOGY_TRAITS, build_morphology_rubric  # noqa: E402
from scripts.build_trait_rubrics import (  # noqa: E402,F401
    _live_wikidata_sparql,
    _resolve_task_ids,
    _resolve_url,
    upsert_rubric,
)


def assemble_all(*, sparql_fn) -> dict:
    """taxon -> assembled+validated trait list, for every authored taxon."""
    return {
        taxon: build_morphology_rubric(taxon, sparql_fn=sparql_fn) for taxon in MORPHOLOGY_TRAITS
    }


def verify_resolves(traits, *, resolve_fn) -> None:
    """Fail loud if any trait's citation URL does not resolve (HTTP 200)."""
    for t in traits:
        url = (t.get("citation") or "").strip()
        if not url or not resolve_fn(url):
            raise ValueError(f"citation did not resolve for {t.get('key')!r}: {url!r}")


def _print_table(rubrics: dict) -> None:
    for taxon, traits in rubrics.items():
        tiers = {}
        for t in traits:
            tiers[t["source_tier"]] = tiers.get(t["source_tier"], 0) + 1
        print(f"\n{taxon}  ({len(traits)} traits, tiers={tiers})")
        for t in traits:
            print(f"  [{t['source_tier']}] {t['trait_class']:13} {t['key']:22} = {t['expected']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commit", action="store_true", help="verify citations live + write to DB")
    args = p.parse_args(argv)

    if not args.commit:
        rubrics = assemble_all(sparql_fn=lambda taxon: None)  # dry: skip network
        _print_table(rubrics)
        total = sum(len(v) for v in rubrics.values())
        print(
            f"\nDRY RUN — {total} traits across {len(rubrics)} taxa, nothing written. "
            "Re-run with --commit (after snapshotting the study DB) to verify + load."
        )
        return 0

    from app.database import SessionLocal

    rubrics = assemble_all(sparql_fn=_live_wikidata_sparql)
    for traits in rubrics.values():
        verify_resolves(traits, resolve_fn=_resolve_url)
    db = SessionLocal()
    try:
        task_ids = _resolve_task_ids(db, {taxon: None for taxon in rubrics})
        for taxon, traits in rubrics.items():
            upsert_rubric(db, taxon, task_ids[taxon], traits)
            print(f"wrote {taxon} (task={task_ids[taxon]}): {len(traits)} traits")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
