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
    assess_independence,
    assess_subject,
    composition_applies,
    morphotype_for,
    qa_reference_image,
    recon_input_photo_ids,
    species_matches,
)

REFERENCE_ROOT = config.ASSET_DIR / "reference"
GALLERY_ROOT = REFERENCE_ROOT / "gallery"
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


def independence_only(slug: str, *, input_photo_ids: set[str]) -> dict:
    """Re-judge ONLY reference-independence over an already-scored manifest. No VLM, no cost.

    Exists because the independence predicate arrived after the corpus was scored, and a full
    re-run would re-pay for every VLM call to change three entries. Verdicts set by the other
    signals are left exactly as they are; this can only ever turn a pass into a fail.
    """
    mpath = GALLERY_ROOT / slug / "manifest.json"
    if not mpath.exists():
        return {"slug": slug, "error": "no manifest"}
    items = json.loads(mpath.read_text())
    flipped = []
    for item in items:
        ind = assess_independence(
            photo_id=item.get("photo_id"),
            url=item.get("url"),
            input_photo_ids=input_photo_ids,
        )
        if ind["ok"]:
            continue
        verdict = qa_reference_image(independence=ind)
        item["passed_qa"] = False
        reasons = [r for r in item.get("qa_reasons", []) if "reconstruction input" not in r]
        item["qa_reasons"] = reasons + verdict["reasons"]
        flipped.append(item.get("file"))
    if flipped:
        mpath.write_text(json.dumps(items, indent=2))
    return {"slug": slug, "n": len(items), "flipped": flipped}


def qa_gallery(slug: str, *, client, bundle, input_photo_ids: set[str] | None = None) -> dict:
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

    panel = list(ORGAN_INVENTORY)
    items = json.loads(mpath.read_text())
    passed = 0
    for item in items:
        png = (mdir / item["file"]).read_bytes()
        # Calibrated reference QA (2026-07-06): the VLM composition question is the fruit-only /
        # isolation signal for ALL taxa — organ-coverage's 'isolated-organ' over-fires on good
        # single-organ views (e.g. an Arabidopsis rosette). Species check via multi-class BioCLIP.
        # Plant/fungus-framed; misfires on animals (see reference_qa.composition_applies).
        composition = (
            assess_composition(client, png, taxon=taxon, common=_common(taxon))
            if composition_applies(inv)
            else None
        )
        species = species_matches(bundle, png, claimed_taxon=taxon, panel=panel) if bundle else None
        # The subject check (2026-07-29) is what makes this gate meaningful without BioCLIP, and
        # asks something BioCLIP cannot: is the claimed organism the photo's MAIN SUBJECT, in the
        # form the task wants? Sourcing only ever guaranteed the identification was correct.
        subject = assess_subject(
            client,
            png,
            taxon=taxon,
            common=_common(taxon),
            morphotype=morphotype_for(taxon),
        )
        # A photo that IS this task's recon input cannot serve as its own reference, however
        # good a picture of the organism it is — and it usually IS a good one, which is why the
        # gathering step picked it too. Pure data comparison, no VLM call.
        independence = assess_independence(
            photo_id=item.get("photo_id"),
            url=item.get("url"),
            input_photo_ids=input_photo_ids,
        )
        verdict = qa_reference_image(
            composition=composition,
            species=species,
            subject=subject,
            independence=independence,
        )
        item["passed_qa"] = verdict["passed"]
        item["qa_reasons"] = verdict["reasons"]
        passed += verdict["passed"]
    mpath.write_text(json.dumps(items, indent=2))
    return {"slug": slug, "taxon": taxon, "n": len(items), "passed": passed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", default="", help="comma slugs (default: all galleries on disk)")
    ap.add_argument(
        "--independence-only",
        action="store_true",
        help="re-judge ONLY reference-independence on already-scored manifests (no VLM, no key)",
    )
    args = ap.parse_args()

    slugs = (
        [s.strip() for s in args.slug.split(",") if s.strip()]
        if args.slug
        else sorted(p.name for p in GALLERY_ROOT.iterdir() if (p / "manifest.json").exists())
    )

    input_photo_ids = recon_input_photo_ids(REFERENCE_ROOT)
    if not input_photo_ids:
        # Fail loud: an empty set makes the predicate abstain, so a silent miss here would read
        # as "every reference is independent" rather than "we could not tell".
        print(
            f"REFUSING: no recon input photos found under {REFERENCE_ROOT} — the independence "
            f"check would abstain on every image and report a clean run."
        )
        return 2
    print(f"{len(input_photo_ids)} recon input photos on record — reference independence enforced")

    if args.independence_only:
        for slug in slugs:
            r = independence_only(slug, input_photo_ids=input_photo_ids)
            if "error" in r:
                print(f"  {slug}: SKIP ({r['error']})")
            elif r["flipped"]:
                print(f"  {slug}: FAILED {len(r['flipped'])} — {', '.join(r['flipped'])}")
            else:
                print(f"  {slug}: clean ({r['n']} entries)")
        return 0

    client = _build_client()
    bundle = species_id.load_model("bioclip") if species_id.available() else None
    if bundle is None:
        print(
            f"NOTE: open_clip unavailable — BioCLIP species check skipped; the VLM subject "
            f"check ({JUDGE_MODEL}) still runs and is the load-bearing species/form gate."
        )

    for slug in slugs:
        r = qa_gallery(slug, client=client, bundle=bundle, input_photo_ids=input_photo_ids)
        if "error" in r:
            print(f"  {slug}: SKIP ({r['error']})")
        else:
            print(f"  {slug} ({r['taxon']}): {r['passed']}/{r['n']} passed QA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
