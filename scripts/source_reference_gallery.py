# scripts/source_reference_gallery.py
"""Source a small per-taxon REFERENCE GALLERY (what the organism looks like, from several
angles/specimens) from iNaturalist's curated per-species `taxon_photos` — species-representative
(correct subject, no observation-misID risk) and license-tagged. Keeps only cc0 / cc-by (skips
cc-by-sa/nc for the redistributable posture). Downloads N medium JPEGs per taxon to
data/assets/reference/gallery/<slug>/ and writes a manifest.json with per-photo CC attribution
(cc-by REQUIRES attribution — shown in the UI). Display-only reference, never a recon input.

Idempotent: a taxon whose gallery dir already has the manifest is skipped unless --force."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GALLERY_ROOT = REPO / "data" / "assets" / "reference" / "gallery"
UA = "bio3d-arena/0.1 (research reference gallery; contact operator)"
OK_LICENSES = {"cc0", "cc-by"}

# The arena's recon taxa (binomial). Rosa is a genus — its gallery is representative garden roses.
TAXA = [
    "Solanum lycopersicum",
    "Zea mays",
    "Rosa",
    "Glycine max",
    "Arabidopsis thaliana",
    "Pinus sylvestris",
    "Lycoperdon perlatum",
    "Cucurbita pepo",
    "Hericium erinaceus",
    "Boletus edulis",
    "Amanita muscaria",
    "Morchella esculenta",
    "Trametes versicolor",
    # Animal kingdom taxa (3rd kingdom).
    "Canis lupus familiaris",
    "Anas platyrhynchos",
    "Danaus plexippus",
    "Carassius auratus",
]


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — fixed https host
        return json.loads(r.read().decode())


def _slug(binomial: str) -> str:
    return binomial.lower().replace(" ", "_")


# Ranks whose photo pool is, by construction, NOT one organism's likeness. `complex` is a group
# of species too similar to separate; anything above genus is broader still. Measured 2026-07-29:
# `q=Rosa` returned the ORDER **Rosales** (brambles, elms, figs, nettles) and three taxa returned
# rank `complex`, between them accounting for 15 of 47 wrong reference photos. The old code took
# `results[0]` from a FUZZY text search and never looked at what came back.
ALLOWED_RANKS = {"species", "subspecies", "variety", "genus"}

# Where the arena's taxon name and iNaturalist's differ. The arena keeps its own name (it is the
# key for organ inventories, gallery slugs and task titles); only the QUERY is translated. Kept
# explicit rather than loosening the name match, which would re-admit the fuzzy hits above.
INAT_NAME = {"Canis lupus familiaris": "Canis familiaris"}


def _resolve_taxon_id(binomial: str) -> int | None:
    """Resolve to a taxon whose NAME MATCHES what was asked and whose rank is narrow enough for
    its photos to depict one organism. A fuzzy top-1 hit is not evidence of either."""
    binomial = INAT_NAME.get(binomial, binomial)
    url = "https://api.inaturalist.org/v1/taxa?" + urllib.parse.urlencode(
        {"q": binomial, "per_page": 10}
    )
    want = binomial.strip().lower()
    for r in _get(url).get("results", []):
        if (r.get("name") or "").strip().lower() == want and r.get("rank") in ALLOWED_RANKS:
            return r["id"]
    return None


def _photo_row(ph: dict) -> dict | None:
    lic = ph.get("license_code") or ""
    if lic not in OK_LICENSES:
        return None
    url = (ph.get("url") or "").replace("square", "medium")
    if not url:
        return None
    return {
        "photo_id": ph.get("id"),
        "license": lic,
        "attribution": ph.get("attribution") or "",
        "url": url,
    }


# Taxa whose arena task depicts the DOMESTICATED form — cultivated crops and pet/farm animals
# alike. iNaturalist's `quality_grade=research` requires an observation to be WILD, so for these
# it systematically selects against the very form the task asks generators to produce. Measured
# 2026-07-29, CC-licensed observations:
#
#   Solanum lycopersicum  20,742 wild /  4,410 captive  -> gallery was feral volunteers in drains
#   Carassius auratus    291,436 wild /  7,199 captive  -> gallery was feral/dead pond fish
#   Canis familiaris       2,084 wild /  2,908 captive  -> gallery was dingoes, dholes, a coyote
#
# For the dog there are MORE captive than wild records: a domestic dog observed "wild" is by
# definition a feral or dingo-type animal, which is why the taxonomic check waved them through.
#
# Deliberately ABSENT:
#   Rosa               2,446 wild /      1 captive  -> no cultivated pool exists; garden-rose
#                      references must come from somewhere other than iNaturalist.
#   Danaus plexippus  33,752 wild /    142 captive  -> genuinely wild; its low pass rate is the
#                      lifecycle question (chrysalis/caterpillar), not the pool.
DOMESTICATED = {
    "Solanum lycopersicum",
    "Zea mays",
    "Glycine max",
    "Cucurbita pepo",
    "Carassius auratus",
    "Canis lupus familiaris",  # the ARENA's name; INAT_NAME translates it for the query
}


def _cc_photos_from_observations(taxon_id: int, n: int, *, cultivated: bool = False) -> list[dict]:
    """Fallback: the curated taxon_photos are only ~7-12 and for some taxa are all NC-licensed
    (e.g. Cucurbita pepo). The observation pool has thousands, many cc0/cc-by — query it with a
    photo-license filter, most-faved first.

    `cultivated` swaps the wild-only research grade for captive/cultivated observations. The two
    are mutually exclusive on iNaturalist: research grade REQUIRES wild, so sending both returns
    an empty set rather than a union."""
    scope = {"captive": "true"} if cultivated else {"quality_grade": "research"}
    url = "https://api.inaturalist.org/v1/observations?" + urllib.parse.urlencode(
        {
            "taxon_id": taxon_id,
            "photo_license": "cc0,cc-by",
            **scope,
            "photos": "true",
            "per_page": max(n * 3, 12),
            "order_by": "votes",
            "order": "desc",
        }
    )
    out: list[dict] = []
    seen: set = set()
    for obs in _get(url).get("results", []):
        for ph in obs.get("photos", []):
            row = _photo_row(ph)
            if row and row["photo_id"] not in seen:
                seen.add(row["photo_id"])
                out.append(row)
                if len(out) >= n:
                    return out
    return out


def _cc_photos(taxon_id: int, n: int, *, cultivated: bool = False) -> list[dict]:
    # For a cultivated taxon the curated taxon_photos are wild-biased too — tomato's first two
    # were urban stormwater drains — so draw straight from the cultivated pool rather than
    # topping up from it, or the wild photos simply fill the gallery first.
    if cultivated:
        return _cc_photos_from_observations(taxon_id, n, cultivated=True)
    res = _get(f"https://api.inaturalist.org/v1/taxa/{taxon_id}").get("results", [])
    photos = res[0].get("taxon_photos", []) if res else []
    out: list[dict] = []
    seen: set = set()
    for tp in photos:
        row = _photo_row(tp.get("photo", {}))
        if row and row["photo_id"] not in seen:
            seen.add(row["photo_id"])
            out.append(row)
            if len(out) >= n:
                return out
    # Top up from the observation pool when the curated taxon_photos have too few CC photos.
    if len(out) < n:
        for row in _cc_photos_from_observations(taxon_id, n - len(out), cultivated=cultivated):
            if row["photo_id"] not in seen:
                seen.add(row["photo_id"])
                out.append(row)
    return out


def source_taxon(binomial: str, n: int, force: bool) -> dict:
    slug = _slug(binomial)
    d = GALLERY_ROOT / slug
    manifest_path = d / "manifest.json"
    if manifest_path.exists() and not force:
        return {
            "taxon": binomial,
            "status": "exists",
            "n": len(json.loads(manifest_path.read_text())),
        }
    tid = _resolve_taxon_id(binomial)
    if tid is None:
        return {"taxon": binomial, "status": "no-taxon"}
    photos = _cc_photos(tid, n, cultivated=binomial in DOMESTICATED)
    if not photos:
        return {"taxon": binomial, "status": "no-cc-photos"}
    d.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, p in enumerate(photos, 1):
        fn = f"{i}.jpg"
        req = urllib.request.Request(p["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            (d / fn).write_bytes(r.read())
        manifest.append({"file": fn, **p})
        time.sleep(0.4)  # polite to iNat
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return {"taxon": binomial, "status": "sourced", "n": len(manifest)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=8, help="photos per taxon (default 8)")
    ap.add_argument("--force", action="store_true", help="re-source even if a manifest exists")
    ap.add_argument("--taxa", default="", help="comma binomials (default: all recon taxa)")
    args = ap.parse_args()
    taxa = [t.strip() for t in args.taxa.split(",") if t.strip()] or TAXA
    for binomial in taxa:
        try:
            print(source_taxon(binomial, args.n, args.force))
        except Exception as e:  # noqa: BLE001 — one taxon never aborts the batch
            print({"taxon": binomial, "status": "error", "error": repr(e)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
