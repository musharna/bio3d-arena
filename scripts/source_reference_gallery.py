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
]


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — fixed https host
        return json.loads(r.read().decode())


def _slug(binomial: str) -> str:
    return binomial.lower().replace(" ", "_")


def _resolve_taxon_id(binomial: str) -> int | None:
    url = "https://api.inaturalist.org/v1/taxa?" + urllib.parse.urlencode(
        {"q": binomial, "per_page": 1}
    )
    res = _get(url).get("results", [])
    return res[0]["id"] if res else None


def _cc_photos(taxon_id: int, n: int) -> list[dict]:
    res = _get(f"https://api.inaturalist.org/v1/taxa/{taxon_id}").get("results", [])
    photos = res[0].get("taxon_photos", []) if res else []
    out = []
    for tp in photos:
        ph = tp.get("photo", {})
        lic = ph.get("license_code") or ""
        if lic not in OK_LICENSES:
            continue
        url = (ph.get("url") or "").replace("square", "medium")
        if not url:
            continue
        out.append(
            {
                "photo_id": ph.get("id"),
                "license": lic,
                "attribution": ph.get("attribution") or "",
                "url": url,
            }
        )
        if len(out) >= n:
            break
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
    photos = _cc_photos(tid, n)
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
    ap.add_argument("--n", type=int, default=3, help="photos per taxon (default 3)")
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
