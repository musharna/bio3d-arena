"""Tests for SDF/MOL molecular subformat support."""

from __future__ import annotations

import pytest

from app import ingest

VALID_SDF = """arena-test
  Bio3DArena

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.5000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000    1.5000    0.0000 N   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
  1  3  1  0  0  0  0
M  END
$$$$
"""


def test_sdf_in_allowed_formats():
    assert "sdf" in ingest.ALLOWED_FORMATS
    assert "sdf" in ingest.MOLECULAR_FORMATS  # routed to the 3Dmol viewer


def test_validate_sdf_ok():
    stats = ingest.validate_asset(VALID_SDF.encode(), "sdf")
    assert stats["kind"] == "molecular"
    assert stats["subformat"] == "sdf"
    assert stats["atoms"] == 3
    assert stats["molecules"] == 1


def test_validate_sdf_zero_atoms_rejected():
    bad = "x\n\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n$$$$\n"
    with pytest.raises(ingest.IngestError):
        ingest.validate_asset(bad.encode(), "sdf")


def test_validate_sdf_garbage_rejected():
    with pytest.raises(ingest.IngestError):
        ingest.validate_asset(b"not a molfile", "sdf")


def test_sdf_content_type():
    from app import storage

    assert storage.content_type_for("ligand.sdf") == "chemical/x-mdl-sdfile"
    assert storage.content_type_for("frag.mol") == "chemical/x-mdl-molfile"


def test_build_molecule_sdf_roundtrips_through_validation(tmp_path):
    from app import ingest
    from app.molec_gen import build_molecule_sdf

    out = tmp_path / "demo.sdf"
    meta = build_molecule_sdf(7, out)
    assert meta["format"] == "sdf"
    assert meta["atoms"] >= 4
    # The generated asset must pass our own ingest validator.
    stats = ingest.validate_asset(out.read_bytes(), "sdf")
    assert stats["atoms"] == meta["atoms"]


def test_seed_creates_sdf_outputs():
    from app.database import SessionLocal
    from app.models import ModelOutput
    from app.seed import seed_all

    seed_all(force=True)
    with SessionLocal() as db:
        n = db.query(ModelOutput).filter_by(asset_format="sdf", is_gold=False).count()
    assert n >= 1  # the ligand-sdf task produced SDF outputs
