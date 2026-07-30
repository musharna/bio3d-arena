# scripts/source_reference_gallery.py
"""Source a small per-taxon REFERENCE GALLERY (what the organism looks like, from several
angles/specimens), by default from iNaturalist's curated per-species `taxon_photos` —
species-representative (correct subject, no observation-misID risk) and license-tagged. Admission
is decided by app.licensing.REDISTRIBUTABLE_LICENSES, never by a local licence list. Downloads N
medium JPEGs per taxon to data/assets/reference/gallery/<slug>/ and writes a manifest.json with
per-photo attribution (CC-BY and CC-BY-SA both REQUIRE it — shown in the UI as the photo credit).
Display-only reference, never a recon input.

A reference has to be REPRESENTATIVE, not merely correctly identified, and those are different
properties: iNaturalist guarantees the identification and says nothing about the framing or the
form. Three pool-selection rules follow from that, each measured against the QA gate on
2026-07-29 — DOMESTICATED (research grade requires WILD), ADULT_ONLY (a caterpillar is not a
butterfly), and COMMONS_SEARCH (a cultivated pool that does not exist on iNaturalist at all).

Idempotent: a taxon whose gallery dir already has the manifest is skipped unless --force."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))  # so `app.licensing` resolves when run as a script

from app.licensing import REDISTRIBUTABLE_LICENSES, normalize_license  # noqa: E402

GALLERY_ROOT = REPO / "data" / "assets" / "reference" / "gallery"
# Wikimedia asks for a descriptive UA naming the project and its purpose, and refuses generic ones.
UA = "bio3d-arena/0.1 (+https://github.com/musharna/bio3d-arena; reference gallery sourcing)"

# iNaturalist states a licence as a short code carrying no version; its CC licences are 4.0.
# Mapping them to SPDX ids means the ONE allowlist in app.licensing decides admission here too.
# This file previously carried `OK_LICENSES = {"cc0", "cc-by"}` — a THIRD hand-maintained copy of
# the redistribution question, of exactly the kind app.licensing warns about ("Import this; never
# re-declare it") after the first two drifted in both directions. It was narrower than the
# allowlist, which is safe but not free: share-alike is IN the allowlist, is already relied on for
# the recon reference photos under the 'redistribute' posture, and is Wikimedia Commons' default
# licence for modern own-work photographs — so excluding it here is what left the rose gallery with
# no sourceable pool at all (measured 2026-07-29: 1 admissible photo per 100 Commons hits without
# it). Share-alike's condition is attribution of an unmodified work, which the manifest carries and
# the gallery UI renders verbatim.
_INAT_LICENCE = {"cc0": "CC0-1.0", "cc-by": "CC-BY-4.0", "cc-by-sa": "CC-BY-SA-4.0"}
_INAT_PHOTO_LICENSE = ",".join(
    code for code, spdx in _INAT_LICENCE.items() if spdx in REDISTRIBUTABLE_LICENSES
)

# What a Commons file may carry. Derived from the allowlist rather than restated, then narrowed to
# the CC families for two content reasons that are not legal ones: PUBLIC-DOMAIN on Commons marks
# digitized historical print — a rose search returned 15 of 16 PD hits as seed-catalogue pages and
# monochrome pre-1930 plates, one of them advertising Sweet William rather than a rose — and
# ODbL-1.0 is a database licence that never applies to a photograph.
GALLERY_LICENSES = frozenset(lic for lic in REDISTRIBUTABLE_LICENSES if lic.startswith("CC"))

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
    lic = _INAT_LICENCE.get(ph.get("license_code") or "")
    if lic not in REDISTRIBUTABLE_LICENSES:
        return None
    url = (ph.get("url") or "").replace("square", "medium")
    if not url:
        return None
    return {
        "photo_id": ph.get("id"),
        "license": lic,
        "attribution": ph.get("attribution") or "",
        "url": url,
        "source": "inaturalist",
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

# Taxa with a metamorphic life cycle, where a juvenile is a DIFFERENT-LOOKING organism and can
# never be a reference for a task asking for the adult. Task 30 asks for "a whole monarch
# butterfly", but 6 of the monarch gallery's 10 rejects were a chrysalis or a caterpillar: a
# caterpillar on milkweed is what people photograph, so the pool is full of them (measured
# 2026-07-29: 39,201 CC research-grade records, of which 9,259 are larvae).
#
# iNaturalist annotates life stage as term_id=1, and value 2 is Adult — 25,614 records remain
# with the filter on. Fixing the POOL rather than leaning on the subject gate to reject each one
# is what keeps the gallery full: candidates the gate rejects still consume a slot.
#
# Deliberately absent: the dog, the mallard and the goldfish have no juvenile stage that reads as
# a different organism, so filtering them would shrink the pool for nothing.
ADULT_ONLY = {"Danaus plexippus"}
_LIFE_STAGE_ADULT = {"term_id": "1", "term_value_id": "2"}


def _cc_photos_from_observations(
    taxon_id: int, n: int, *, cultivated: bool = False, adult_only: bool = False
) -> list[dict]:
    """Fallback: the curated taxon_photos are only ~7-12 and for some taxa are all NC-licensed
    (e.g. Cucurbita pepo). The observation pool has thousands, many cc0/cc-by — query it with a
    photo-license filter, most-faved first.

    `cultivated` swaps the wild-only research grade for captive/cultivated observations. The two
    are mutually exclusive on iNaturalist: research grade REQUIRES wild, so sending both returns
    an empty set rather than a union.

    `adult_only` adds the Life Stage = Adult annotation. Only for ADULT_ONLY taxa: iNaturalist
    answers an inapplicable annotation with an empty set, so sending it for a pine would empty
    the gallery silently rather than fail."""
    scope = {"captive": "true"} if cultivated else {"quality_grade": "research"}
    if adult_only:
        scope |= _LIFE_STAGE_ADULT
    url = "https://api.inaturalist.org/v1/observations?" + urllib.parse.urlencode(
        {
            "taxon_id": taxon_id,
            "photo_license": _INAT_PHOTO_LICENSE,
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


def _cc_photos(
    taxon_id: int, n: int, *, cultivated: bool = False, adult_only: bool = False
) -> list[dict]:
    # For a cultivated taxon the curated taxon_photos are wild-biased too — tomato's first two
    # were urban stormwater drains — so draw straight from the cultivated pool rather than
    # topping up from it, or the wild photos simply fill the gallery first. `adult_only` bypasses
    # them for the same structural reason plus a harder one: taxon_photos cannot be
    # annotation-filtered at all, and for the monarch they are where the chrysalis shots live.
    if cultivated or adult_only:
        return _cc_photos_from_observations(
            taxon_id, n, cultivated=cultivated, adult_only=adult_only
        )
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


# --- Wikimedia Commons ---------------------------------------------------------------------
#
# A second source, for taxa iNaturalist STRUCTURALLY cannot supply. Only Rosa qualifies: the
# arena's rose tasks depict a cultivated garden rose (task 19's own input photo is a pink garden
# shrub against a barn), and iNaturalist holds 2,446 wild CC-licensed Rosa records against exactly
# ONE cultivated. No filter recovers a pool that does not exist. Commons, being a media archive
# rather than a wildlife-observation platform, is full of garden roses.
#
# This is an escape hatch, not a replacement: iNaturalist records carry a community-verified
# identification, which a Commons filename does not, so every other taxon stays there.
COMMONS_API = "https://commons.wikimedia.org/w/api.php?"

# `-incategory:` excludes Commons' own scanned-catalogue category. Bare word negation does NOT
# work here — '-catalog -illustration' cut the same search from 100 hits to 1, because every
# rose photo whose description mentions a catalogue goes with it. Structured negation is exact.
COMMONS_SEARCH = {"Rosa": 'filetype:bitmap rose bush garden bloom -incategory:"Seed catalogs"'}

# Digitised print wearing a modern licence. The USDA/Internet Archive seed-catalogue scans were
# bulk-uploaded under CC-BY-2.0 by the scanning institution, so a licence test cannot see them:
# six of the first eight rose photos were Victorian catalogue pages, one of them advertising Sweet
# William. Matched against the file's Artist and Credit, which is where the collection is named.
COMMONS_SKIP_COLLECTIONS = ("seed trade catalog", "nursery and seed", "seed catalog")

# MediaWiki resolves `prop=imageinfo` for at most 50 titles per request. Ask for more and the
# surplus pages come back WITHOUT imageinfo — every licence unknown, every photo dropped, and the
# result is indistinguishable from 'Commons has nothing'. Asking for 200 returned 200 hits and 0
# admissible photos. So the page size is capped and the walk continues instead.
_COMMONS_PAGE = 50
_COMMONS_MAX_PAGES = 8

_TAG = re.compile(r"<[^>]*>")


def _commons_licence(label: str | None) -> str | None:
    """Commons states a licence as a human label — 'CC BY 4.0', 'CC0', 'Public domain', and the
    ambiguous 'No restrictions' it uses for files whose provenance is merely unchallenged. Feed
    them through the same normalizer as every other licence string so the vocabulary lives in one
    place; anything that does not land on an SPDX id we allow is dropped by the caller."""
    return normalize_license(label)


def _plain_text(raw: str) -> str:
    """Commons returns `Artist` as HTML (an anchor to the uploader's user page). The credit line is
    rendered as text, so markup would show up verbatim in the gallery."""
    return " ".join(html.unescape(_TAG.sub("", raw or "")).split())


def _commons_row(page: dict) -> dict | None:
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    lic = _commons_licence((meta.get("LicenseShortName") or {}).get("value"))
    if lic not in GALLERY_LICENSES:
        return None
    url = info.get("thumburl") or info.get("url") or ""
    if not url:
        return None
    artist = _plain_text((meta.get("Artist") or {}).get("value") or "")
    provenance = f"{artist} {_plain_text((meta.get('Credit') or {}).get('value') or '')}".lower()
    if any(c in provenance for c in COMMONS_SKIP_COLLECTIONS):
        return None
    credit = f"{artist} — {lic} via Wikimedia Commons" if artist else f"{lic} via Wikimedia Commons"
    return {
        "photo_id": page.get("pageid"),
        "license": lic,
        "attribution": credit,
        "url": url,
        "source": "wikimedia-commons",
        "page": info.get("descriptionurl") or "",
    }


def _commons_photos(search: str, n: int) -> list[dict]:
    """Search Commons' File: namespace and keep the photos we may redistribute. `extmetadata` comes
    back with the file so the licence is known before anything is downloaded — asking per file
    would be a round trip each, and would tempt fetching first and checking after.

    Walks the search with the API's own continuation, because one 50-file page yields only a
    handful of admissible photos (~8 per 60 hits for roses, most of the rest being share-alike-free
    licences we cannot use or scanned catalogues) and a 16-photo gallery would never fill."""
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": search,
        "gsrnamespace": "6",  # File: — without this the search returns wiki articles
        "gsrlimit": str(_COMMONS_PAGE),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": "1024",
    }
    out: list[dict] = []
    seen: set = set()
    cont: dict = {}
    for _ in range(_COMMONS_MAX_PAGES):
        resp = _get(COMMONS_API + urllib.parse.urlencode({**params, **cont}))
        for page in ((resp.get("query") or {}).get("pages") or {}).values():
            row = _commons_row(page)
            if row and row["photo_id"] not in seen:
                seen.add(row["photo_id"])
                out.append(row)
                if len(out) >= n:
                    return out
        cont = resp.get("continue") or {}
        if not cont:
            break
        time.sleep(0.4)  # polite to Wikimedia between pages
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
    search = COMMONS_SEARCH.get(binomial)
    if search:
        photos = _commons_photos(search, n)
    else:
        tid = _resolve_taxon_id(binomial)
        if tid is None:
            return {"taxon": binomial, "status": "no-taxon"}
        photos = _cc_photos(
            tid,
            n,
            cultivated=binomial in DOMESTICATED,
            adult_only=binomial in ADULT_ONLY,
        )
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
