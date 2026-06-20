"""Structure-validation tests: parser, reference-free stereochemistry, and a
real-execution check on the bundled real 1CRN crystal structure."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import validation

BENCH = Path(__file__).resolve().parent.parent / "app" / "data" / "benchmarks" / "assets"

PDB_3 = (
    "ATOM      1  N   THR A   1      17.047  14.099   3.625  1.00 13.79           N  \n"
    "ATOM      2  CA  THR A   1      16.967  12.784   4.338  1.00 10.80           C  \n"
    "ATOM      3  C   THR A   1      15.685  12.755   5.133  1.00  9.19           C  \n"
)
SDF_3 = (
    "demo\n  Bio3DArena\n\n"
    "  3  2  0  0  0  0  0  0  0  0999 V2000\n"
    "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    1.5400    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    3.0800    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "  1  2  1  0  0  0  0\n  2  3  1  0  0  0  0\nM  END\n$$$$\n"
)


def test_parse_pdb_atoms():
    atoms = validation.parse_atoms(PDB_3, "pdb")
    assert [a.element for a in atoms] == ["N", "C", "C"]
    assert atoms[0].name == "N" and atoms[1].name == "CA"
    np.testing.assert_allclose(atoms[0].xyz, [17.047, 14.099, 3.625], atol=1e-3)


def test_parse_sdf_atoms():
    atoms = validation.parse_atoms(SDF_3, "sdf")
    assert [a.element for a in atoms] == ["C", "C", "C"]
    np.testing.assert_allclose(atoms[1].xyz, [1.54, 0.0, 0.0], atol=1e-3)


def test_parse_empty_raises():
    with pytest.raises(validation.ValidationError):
        validation.parse_atoms("not a structure\n", "pdb")


def test_clashscore_flags_overlapping_atoms():
    # Two non-bonded carbons 0.5 Å apart → severe clash.
    clash = (
        "HETATM    1  C1  LIG A   1       0.000   0.000   0.000  1.00  0.00           C  \n"
        "HETATM    2  C2  LIG A   1       0.500   0.000   0.000  1.00  0.00           C  \n"
    )
    r = validation.validate_structure(clash, "pdb")
    assert r["clashes_per_1000"] > 0
    assert r["tier"] in ("minor", "major")


def test_clean_chain_has_no_clashes():
    r = validation.validate_structure(SDF_3, "sdf")
    assert r["clashes_per_1000"] == 0
    assert r["bond_outliers"] == 0
    assert r["tier"] == "clean"


def test_bond_outlier_flags_stretched_bond():
    bad = (
        "demo\n  Bio3DArena\n\n"
        "  2  1  0  0  0  0  0  0  0  0999 V2000\n"
        "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "    3.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "  1  2  1  0  0  0  0\nM  END\n$$$$\n"
    )
    r = validation.validate_structure(bad, "sdf")
    assert r["bond_outliers"] >= 1


def test_rama_is_none_for_small_molecule():
    r = validation.validate_structure(SDF_3, "sdf")
    assert r["rama_outliers"] is None


def test_real_1crn_parses_and_scores_sane():
    text = (BENCH / "1crn.pdb").read_text()
    r = validation.validate_structure(text, "pdb")
    assert r["status"] == "ok"
    assert r["n_atoms"] == 327  # real crambin entry
    # a real 1.5 Å crystal structure must not read as garbage
    assert r["tier"] in ("clean", "minor"), r
    assert r["rama_outliers"] is not None  # it IS a protein chain
