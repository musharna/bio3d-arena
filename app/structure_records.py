"""Where a structure-known output's organ record comes from (Mode-B organ-fidelity axis).

Two sources, in priority order (§ contract 2026-06-26 "where the organ record comes from"):

  1. A ``…__structure.json`` sidecar next to the output's GLB — the clean ingest contract.
     AgriGen's pd-archetype export emits it (the /score_structure request body verbatim); we
     ingest it as-is. Until that sidecar lands for a given output, we fall back to:
  2. The 5 *seed-PD records* below — the organ structure of AgriGen's seed plant descriptors,
     keyed by species slug. These are the exact request bodies the AgriGen seed PDs produce via
     ``agrigen.eval.organ_structure.record_to_score_request`` (mirrored from that repo's
     ``data/eval/organ_structure_score/report.json``), so POSTing them reproduces AgriGen's own
     verdict. bio3d never imports AgriGen (microservice boundary D1) — it ships these records to
     the /score_structure endpoint.

The pine caveat is encoded honestly here: ``pinus_sylvestris`` carries ``needles_per_fascicle:
None`` — AgriGen's pine PD does NOT model needles-per-fascicle, so the attribute resolves to a
structural gap → fidelity 0.0. We do NOT hand-type ``needles_per_fascicle: 2`` (that would
launder a real structural gap into a PASS; the reference key ≠ the extractor's field).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# The full /score_structure request-body field set (mirrors AgriGen's StructureScoreRequest).
# Every record below is normalized to all seven keys so the POST body is explicit; an absent
# organ is ``None`` (a structural gap / not-modeled), never an omission.
_RECORD_FIELDS = (
    "leaf_axis_count",
    "leaf_axis_phyllotaxy_deg",
    "leaflets_per_leaf",
    "needles_per_fascicle",
    "primary_branch_count",
    "primary_branching_angle_deg",
)

# Seed-PD records for the 5 species the AgriGen botanical reference currently covers. Values
# are the seed PDs' extracted structure (report.json, 2026-06-26). A 0.0 below is a real,
# pre-registered structural gap — a valid finding, displayed, not suppressed.
SEED_PD_RECORDS: dict[str, dict] = {
    # zea_mays: 18 distichous (180°) leaves → 1.0.
    "zea_mays": {"leaf_axis_count": 18, "leaf_axis_phyllotaxy_deg": 180.0},
    # arabidopsis_thaliana: 10 rosette leaves, golden-angle (137.5°) → 1.0.
    "arabidopsis_thaliana": {"leaf_axis_count": 10, "leaf_axis_phyllotaxy_deg": 137.5},
    # solanum_lycopersicum: 7 leaflets/leaf, spiral 137.5° → 1.0. (leaf_axis_count is not in
    # tomato's reference and not asserted here — graded result is unaffected.)
    "solanum_lycopersicum": {"leaflets_per_leaf": 7, "leaf_axis_phyllotaxy_deg": 137.5},
    # pinus_sylvestris: PD does NOT model needles-per-fascicle → structural gap → 0.0 (caveat).
    "pinus_sylvestris": {"needles_per_fascicle": None},
    # fagus_sylvatica: PD has no leaf axis (foliage not modeled) → 0.0 (structural gap).
    "fagus_sylvatica": {},
}

# Procedural-generator ``meta_json['variant']`` → species slug, a fallback species resolver for
# procedural outputs NOT on a recon Task (which has no ReconTask species slug). Only species
# present in SEED_PD_RECORDS yield a record; others resolve to None (no declared record → "—").
VARIANT_SPECIES: dict[str, str] = {
    "tomato": "solanum_lycopersicum",
    "maize": "zea_mays",
    "rose": "rosa_canina",  # no reference yet → honest N/A
    "soybean": "glycine_max",  # no reference yet → honest N/A
}


def seed_record_for_species(species_slug: str | None) -> dict | None:
    """The /score_structure request body for a covered species' seed PD, or None if the
    species has no seed-PD record (un-covered → no declared structure to score)."""
    if not species_slug:
        return None
    rec = SEED_PD_RECORDS.get(species_slug)
    if rec is None:
        return None
    body = {f: None for f in _RECORD_FIELDS}
    body.update(rec)
    return {"species": species_slug, **body}


def _sidecar_rel_path(asset_path: str) -> str:
    """The structure-sidecar path beside an asset: ``a/b/foo.glb`` → ``a/b/foo__structure.json``."""
    base = asset_path.rsplit(".", 1)[0] if "." in asset_path.rsplit("/", 1)[-1] else asset_path
    return f"{base}__structure.json"


def load_sidecar(storage, asset_path: str) -> dict | None:
    """A ``…__structure.json`` organ record sitting next to the GLB, ingested VERBATIM, or
    None if absent. Best-effort: a malformed/missing sidecar is not an error here — the caller
    falls back to the seed record. The sidecar must carry a 'species' key (it is the
    /score_structure request body)."""
    rel = _sidecar_rel_path(asset_path)
    try:
        raw = storage.read(rel)
    except Exception:  # noqa: BLE001 — absent sidecar is the common case (not yet emitted)
        return None
    try:
        rec = json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.warning("structure sidecar %s is not valid JSON: %s", rel, e)
        return None
    if not isinstance(rec, dict) or "species" not in rec:
        logger.warning("structure sidecar %s missing 'species' key; ignoring", rel)
        return None
    return rec
