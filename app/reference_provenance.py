"""Enforce that every recon input reference photo has a cleared CC provenance sidecar before it
may be published (even display). A render of a derivative of a copyrighted photo is still a display
of that photo's work — so an uncleared reference photo blocks its recon outputs."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from . import config
from .models import ModelOutput

_CC_OK = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0"}
_REQUIRED = {
    "subject",
    "file",
    "source",
    "source_url",
    "download_url",
    "license",
    "author",
    "attribution",
    "title",
    "note",
}


class ReferenceProvenanceError(RuntimeError):
    pass


def _ref_dir():
    return config.ASSET_DIR / "reference"


def cleared_reference_taxa() -> set[str]:
    """Taxa with a valid CC sidecar {taxon}_ref.json (all required fields, allowlisted license)."""
    ok: set[str] = set()
    d = _ref_dir()
    if not d.exists():
        return ok
    from .licensing import normalize_license

    for meta in d.glob("*_ref.json"):
        try:
            data = json.loads(meta.read_text())
        except Exception:
            continue
        if (
            _REQUIRED <= set(data)
            and all(
                str(data.get(k, "")).strip() for k in ("author", "attribution", "title", "subject")
            )
            and normalize_license(data.get("license")) in _CC_OK
        ):
            taxon = meta.name[: -len("_ref.json")]
            ok.add(taxon)
    return ok


def _taxon_of(input_image: str | None) -> str | None:
    if not input_image:
        return None
    m = re.search(r"([a-z0-9]+)_ref", input_image.lower())
    return m.group(1) if m else None


def assert_recon_photos_cleared(db: Session, output_ids: set[int]) -> None:
    """Raise if any recon output in the set uses a reference photo whose taxon lacks a cleared sidecar."""
    from .public_export import _COMMERCIAL_MODEL_PREFIXES

    cleared = cleared_reference_taxa()
    for oid in sorted(output_ids):
        o = db.get(ModelOutput, oid)
        if o is None or not (o.source or "").startswith(_COMMERCIAL_MODEL_PREFIXES):
            continue
        img = (json.loads(o.meta_json or "{}")).get("input_image")
        taxon = _taxon_of(img)
        if taxon is None:
            raise ReferenceProvenanceError(
                f"output {oid}: recon input '{img}' has no identifiable reference-photo taxon —"
                " cannot verify provenance"
            )
        if taxon not in cleared:
            raise ReferenceProvenanceError(
                f"output {oid}: recon input '{img}' (taxon {taxon!r}) has no cleared CC provenance sidecar"
            )
