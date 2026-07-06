# scripts/add_reference_photo.py
"""Ingest an OWNED / CC-licensed reference photo as a recon input: copy it to
{taxon}_ref_clean.jpg and write the provenance sidecar as {taxon}_ref.json — the sidecar name
MUST end in `_ref.json` because reference_provenance.cleared_reference_taxa() globs `*_ref.json`
and derives the cleared taxon from that filename (a `_ref_clean.json` sidecar would be silently
skipped, so the taxon would never clear). Does NOT touch the DB or call any paid regen — after
this, run:
  scripts/generate_api_recon.py --crop {taxon} --force   (then completeness scoring)
and point CROPS[{taxon}] at {taxon}_ref_clean.jpg if not already."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from app.licensing import normalize_license
from app.reference_provenance import _CC_OK, _REQUIRED


def write_reference_photo(
    *,
    taxon: str,
    image_path: Path,
    author: str,
    license_: str,
    source_url: str,
    download_url: str,
    subject: str,
    title: str,
    note: str,
    dest_dirs: list[Path],
    force: bool = False,
) -> Path:
    norm = normalize_license(license_)
    if norm not in _CC_OK:
        raise ValueError(
            f"license {license_!r} normalizes to {norm!r}, not in the CC allowlist {sorted(_CC_OK)}"
        )
    sidecar = {
        "subject": subject,
        "file": f"{taxon}_ref_clean.jpg",
        "source": source_url.split("/")[2] if "//" in source_url else source_url,
        "source_url": source_url,
        "download_url": download_url,
        "license": norm,
        "author": author,
        "attribution": f"{title} by {author}, {norm}",
        "title": title,
        "note": note,
    }
    missing = _REQUIRED - set(sidecar)
    if missing:
        raise ValueError(f"sidecar missing required fields: {sorted(missing)}")
    if not all(str(sidecar[k]).strip() for k in ("author", "attribution", "title", "subject")):
        raise ValueError("author/attribution/title/subject must all be non-empty")

    first_jpg = None
    for d in dest_dirs:
        d.mkdir(parents=True, exist_ok=True)
        jpg = d / f"{taxon}_ref_clean.jpg"
        # Sidecar MUST be {taxon}_ref.json (not _ref_clean.json) so cleared_reference_taxa()'s
        # `*_ref.json` glob scans it and derives taxon={taxon}. The `file` field still points at
        # the actual {taxon}_ref_clean.jpg image.
        sidecar_path = d / f"{taxon}_ref.json"
        # Guard the SIDECAR path too (not just the image): a taxon may already have a curated
        # original {taxon}_ref.json (e.g. morel_ref.json = Philip Precey/iNat/CC0) even when no
        # _ref_clean.jpg exists yet — writing unconditionally would silently destroy that
        # provenance record. Refuse without force if EITHER target exists.
        if not force and (jpg.exists() or sidecar_path.exists()):
            raise FileExistsError(
                f"{jpg} or {sidecar_path} exists — use force=True to overwrite (this can replace a "
                f"curated original {taxon}_ref.json provenance record)"
            )
        shutil.copyfile(image_path, jpg)
        sidecar_path.write_text(json.dumps(sidecar, indent=2))
        first_jpg = first_jpg or jpg
    assert first_jpg is not None
    return first_jpg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--taxon", required=True)
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--author", required=True)
    ap.add_argument("--license", dest="license_", required=True)
    ap.add_argument("--source-url", required=True)
    ap.add_argument("--download-url", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--note", default="Owner-supplied CC reference photo.")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    from app import config

    main_ref = config.ASSET_DIR / "reference"
    dest_dirs = [main_ref]
    out = write_reference_photo(
        taxon=a.taxon,
        image_path=a.image,
        author=a.author,
        license_=a.license_,
        source_url=a.source_url,
        download_url=a.download_url,
        subject=a.subject,
        title=a.title,
        note=a.note,
        dest_dirs=dest_dirs,
        force=a.force,
    )
    print(
        f"wrote {out} + sidecar. Next: point CROPS[{a.taxon!r}] at {a.taxon}_ref_clean.jpg, "
        f"then `python scripts/generate_api_recon.py --crop {a.taxon} --force` + score completeness."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
