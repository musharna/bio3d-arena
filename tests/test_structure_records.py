"""Unit tests for the Mode-B organ-record source layer (seed-PD records + sidecar)."""

from __future__ import annotations

import json

from app import structure_records as sr


def test_seed_record_normalizes_all_fields():
    rec = sr.seed_record_for_species("zea_mays")
    assert rec["species"] == "zea_mays"
    assert rec["leaf_axis_count"] == 18
    assert rec["leaf_axis_phyllotaxy_deg"] == 180.0
    # Every /score_structure field present (explicit None for absent organs).
    for f in sr._RECORD_FIELDS:
        assert f in rec
    assert rec["leaflets_per_leaf"] is None
    assert rec["needles_per_fascicle"] is None


def test_pine_record_does_not_launder_the_structural_gap():
    """The pine caveat: needles_per_fascicle stays None (AgriGen's PD doesn't model it) so the
    score is an honest 0.0 — never a hand-typed 2 that would fake a PASS."""
    rec = sr.seed_record_for_species("pinus_sylvestris")
    assert rec["needles_per_fascicle"] is None


def test_uncovered_species_has_no_seed_record():
    assert sr.seed_record_for_species("rosa_canina") is None
    assert sr.seed_record_for_species(None) is None
    assert sr.seed_record_for_species("") is None


def test_all_five_covered_species_present():
    assert set(sr.SEED_PD_RECORDS) == {
        "zea_mays",
        "arabidopsis_thaliana",
        "solanum_lycopersicum",
        "pinus_sylvestris",
        "fagus_sylvatica",
    }


class _FakeStorage:
    def __init__(self, files: dict[str, bytes]):
        self._files = files

    def read(self, rel: str) -> bytes:
        return self._files[rel]  # raises KeyError when absent (like a missing object)


def test_sidecar_path_derivation_and_verbatim_load():
    body = {"species": "Zea mays", "leaf_axis_count": 18}
    storage = _FakeStorage({"seed/foo__structure.json": json.dumps(body).encode()})
    assert sr._sidecar_rel_path("seed/foo.glb") == "seed/foo__structure.json"
    assert sr.load_sidecar(storage, "seed/foo.glb") == body


def test_sidecar_absent_or_malformed_returns_none():
    assert sr.load_sidecar(_FakeStorage({}), "seed/foo.glb") is None
    bad = _FakeStorage({"seed/foo__structure.json": b"{not json"})
    assert sr.load_sidecar(bad, "seed/foo.glb") is None
    no_species = _FakeStorage({"seed/foo__structure.json": b'{"leaf_axis_count": 1}'})
    assert sr.load_sidecar(no_species, "seed/foo.glb") is None
