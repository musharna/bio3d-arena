"""Enforce that every recon input reference photo has a cleared provenance sidecar before its recon
outputs may be REDISTRIBUTED: a redistributed recon mesh is a derivative of its input photo, so
that photo must itself be redistributable. Display is exempt — showing an AI-labeled mesh with no
download does not redistribute the photo — and export_public calls this gate only under posture
'redistribute' (scripts/export_public.py). Under display the uncleared photo is instead suppressed
from the vote UI (service.reference_images_for_task): show the mesh, never the photo."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from . import config
from .licensing import REDISTRIBUTABLE_LICENSES
from .models import ModelOutput

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


def cleared_reference_images() -> set[str]:
    """Filenames of reference photos carrying a valid CC provenance record.

    Keyed by the IMAGE a record covers -- its `file` field -- NOT by taxon. Copyright attaches to
    an individual photograph, so a cleared photo must never launder a different, unrecorded photo
    that merely shares a taxon prefix. The previous taxon key was wrong in BOTH directions:
    `tomato_ref_roma.jpg` (7 outputs) read as cleared because a *different* tomato photo had a
    record, while `rose_ref_clean.jpg` / `soybean_ref_clean.jpg` (21 outputs) read as uncleared
    even though their records were present and valid -- those sidecars are named off the
    `{taxon}_ref.json` convention, so the old `*_ref.json` glob never saw them.

    A record's own filename is therefore irrelevant; only its `file` field is. `*_old` records are
    skipped: they describe a superseded photo, yet their `file` field can still name the current
    one, which would attach stale provenance to a live image.
    """
    ok: set[str] = set()
    d = _ref_dir()
    if not d.exists():
        return ok
    from .licensing import normalize_license

    for meta in d.glob("*_ref*.json"):
        if meta.stem.endswith("_old"):
            continue
        try:
            data = json.loads(meta.read_text())
        except Exception:
            continue
        if (
            _REQUIRED <= set(data)
            and all(
                str(data.get(k, "")).strip() for k in ("author", "attribution", "title", "subject")
            )
            and normalize_license(data.get("license")) in REDISTRIBUTABLE_LICENSES
        ):
            covered = str(data.get("file") or "").strip()
            if covered:
                ok.add(covered)
    return ok


def _image_name(input_image: str | None) -> str | None:
    """Basename of a recorded recon input, e.g. 'reference/rose_ref.jpg' -> 'rose_ref.jpg'."""
    if not input_image:
        return None
    return PurePosixPath(str(input_image)).name or None


def assert_recon_photos_cleared(db: Session, output_ids: set[int]) -> None:
    """Raise if any recon output in the set uses a reference photo that lacks a cleared sidecar."""
    from .public_export import _COMMERCIAL_MODEL_PREFIXES

    cleared = cleared_reference_images()
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
        name = _image_name(img)
        if name is None:
            raise ReferenceProvenanceError(
                f"output {oid}: recon input '{img}' has no identifiable reference-photo filename —"
                " cannot verify provenance"
            )
        if name not in cleared:
            raise ReferenceProvenanceError(
                f"output {oid}: recon input '{img}' has no cleared CC provenance record for"
                f" {name!r} (a record for another photo of the same taxon does not cover it)"
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

    cleared = cleared_reference_images()
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
        img = (json.loads(asset.meta_json or "{}")).get("input_image")
        # Same recon-to-verify test as assert_recon_photos_cleared: a commercial-model asset OR a
        # bio3d-arena internal recon (identified by a recorded input_image; a GT mesh/scan has
        # none). A gold row aliasing a non-CC bio3d-arena recon twin must not slip past unchecked.
        is_commercial = (asset.source or "").startswith(_COMMERCIAL_MODEL_PREFIXES)
        is_internal_recon = (asset.source == "bio3d-arena") and (img is not None)
        if not (is_commercial or is_internal_recon):
            continue
        name = _image_name(img)
        if name is None:
            raise ReferenceProvenanceError(
                f"gold output {oid} (twin asset {asset.id}): recon input '{img}' has no"
                " identifiable reference-photo filename — cannot verify provenance"
            )
        if name not in cleared:
            raise ReferenceProvenanceError(
                f"gold output {oid} (twin asset {asset.id}): recon input '{img}' has no cleared"
                f" CC provenance record for {name!r}"
            )
