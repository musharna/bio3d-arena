"""Score reference-gallery images and write `passed_qa` / `qa_reasons` into each manifest, so
`reference_images_for_task` only shows QA-passed anchors. Mechanism routing (per the 2026-07-06
probe): plant (_inv, >=2 required organs) fruit-only via organ-coverage VLM; body-plan (_body_inv:
fungi/gourd) fruit-only via the direct VLM composition question; species check via multi-class
BioCLIP when open_clip is available. Read-only w.r.t. the DB. GPU for the species check → jobd.

Usage:
  ANTHROPIC_API_KEY=... python scripts/qa_reference_gallery.py --slug solanum_lycopersicum,rosa
  ANTHROPIC_API_KEY=... python scripts/qa_reference_gallery.py            # all galleries
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, species_id  # noqa: E402
from app.judge import JUDGE_MODEL  # noqa: E402
from app.organ_inventory import ORGAN_INVENTORY, inventory_for  # noqa: E402
from app.reference_qa import (  # noqa: E402
    assess_composition,
    assess_organ_coverage,
    qa_reference_image,
    species_matches,
)

GALLERY_ROOT = config.ASSET_DIR / "reference" / "gallery"
_SLUG_TO_TAXON = {t.lower().replace(" ", "_"): t for t in ORGAN_INVENTORY}


def _common(taxon: str) -> str:
    try:
        from app.commission import SPECIES_COMMON

        return SPECIES_COMMON.get(taxon, taxon)
    except Exception:
        return taxon


def _build_client():
    import os

    import anthropic

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def qa_gallery(slug: str, *, client, bundle) -> dict:
    taxon = _SLUG_TO_TAXON.get(slug)
    if taxon is None:
        return {"slug": slug, "error": f"no taxon for slug {slug!r}"}
    inv = inventory_for(taxon)
    if inv is None:
        return {"slug": slug, "error": f"no inventory for {taxon!r}"}
    mdir = GALLERY_ROOT / slug
    mpath = mdir / "manifest.json"
    if not mpath.exists():
        return {"slug": slug, "error": "no manifest"}

    n_required = sum(1 for o in inv.organs if o.required)
    is_plant = n_required >= 2
    panel = list(ORGAN_INVENTORY)
    items = json.loads(mpath.read_text())
    passed = 0
    for item in items:
        png = (mdir / item["file"]).read_bytes()
        organ = (
            assess_organ_coverage(client, png, inventory=inv)
            if is_plant
            else {"fruit_only": None, "category": None}
        )
        composition = (
            None
            if is_plant
            else assess_composition(client, png, taxon=taxon, common=_common(taxon))
        )
        species = species_matches(bundle, png, claimed_taxon=taxon, panel=panel) if bundle else None
        verdict = qa_reference_image(organ=organ, composition=composition, species=species)
        item["passed_qa"] = verdict["passed"]
        item["qa_reasons"] = verdict["reasons"]
        passed += verdict["passed"]
    mpath.write_text(json.dumps(items, indent=2))
    return {"slug": slug, "taxon": taxon, "n": len(items), "passed": passed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="", help="comma slugs (default: all galleries on disk)")
    args = ap.parse_args()

    slugs = (
        [s.strip() for s in args.slug.split(",") if s.strip()]
        if args.slug
        else sorted(p.name for p in GALLERY_ROOT.iterdir() if (p / "manifest.json").exists())
    )
    client = _build_client()
    bundle = species_id.load_model("bioclip") if species_id.available() else None
    if bundle is None:
        print(f"NOTE: open_clip unavailable — species check skipped (VLM {JUDGE_MODEL} only)")

    for slug in slugs:
        r = qa_gallery(slug, client=client, bundle=bundle)
        if "error" in r:
            print(f"  {slug}: SKIP ({r['error']})")
        else:
            print(f"  {slug} ({r['taxon']}): {r['passed']}/{r['n']} passed QA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
