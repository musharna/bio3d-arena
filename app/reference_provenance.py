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
        if o is None:
            continue
        img = (json.loads(o.meta_json or "{}")).get("input_image")
        is_commercial = (o.source or "").startswith(_COMMERCIAL_MODEL_PREFIXES)
        # A recon derives from its input photo regardless of who ran it. Commercial-model recon
        # is always a recon; a bio3d-arena output is a recon iff it recorded an input_image (a
        # held-out GT mesh / scan has none) — those internal recons must ALSO be input-cleared to
        # ship in the dataset, else an internal recon from a non-CC photo would slip through.
        is_internal_recon = (o.source == "bio3d-arena") and (img is not None)
        if not (is_commercial or is_internal_recon):
            continue
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


def assert_recon_photos_cleared_for_gold(db: Session, gold_output_ids: set[int]) -> None:
    """Raise if any gold output's underlying non-gold twin (the ModelOutput sharing its
    asset_path, per effective_provenance's resolution) is a commercial-model recon whose
    reference photo lacks a cleared CC sidecar. A gold output's own `source`/`meta_json` are
    calibration-generator decoy values (see public_export.effective_provenance) -- the twin's
    asset is what actually ships under the gold row, so the twin's reference photo -- not the
    gold row's own (absent) one -- must be checked."""
    from sqlalchemy import select

    from .public_export import _COMMERCIAL_MODEL_PREFIXES

    cleared = cleared_reference_taxa()
    for oid in sorted(gold_output_ids):
        o = db.get(ModelOutput, oid)
        if o is None:
            continue
        twin = (
            db.execute(
                select(ModelOutput).where(
                    ModelOutput.asset_path == o.asset_path,
                    ModelOutput.is_gold.is_(False),
                )
            )
            .scalars()
            .first()
        )
        asset = twin if twin is not None else o
        if not (asset.source or "").startswith(_COMMERCIAL_MODEL_PREFIXES):
            continue
        img = (json.loads(asset.meta_json or "{}")).get("input_image")
        taxon = _taxon_of(img)
        if taxon is None:
            raise ReferenceProvenanceError(
                f"gold output {oid} (twin asset {asset.id}): recon input '{img}' has no"
                " identifiable reference-photo taxon — cannot verify provenance"
            )
        if taxon not in cleared:
            raise ReferenceProvenanceError(
                f"gold output {oid} (twin asset {asset.id}): recon input '{img}'"
                f" (taxon {taxon!r}) has no cleared CC provenance sidecar"
            )
