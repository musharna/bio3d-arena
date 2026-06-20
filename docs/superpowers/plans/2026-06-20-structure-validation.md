# Structure Validation Track Implementation Plan (Increment 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an objective, computed structure-validation track (reference-free stereochemistry + reference-based similarity) for molecular outputs, surfaced on a dedicated `/validation` page distinct from the aesthetic vote.

**Architecture:** A pure-Python `app/validation.py` (atom parser + `validate_structure` + `compare_to_reference` + `validate_output` + a `perturb_pdb` util) with zero DB/IO deps. A thin `app/validation_service.py` does the DB wiring: read asset bytes, compute, store under `meta_json["validation"]`, plus aggregations for the page. Routes (`/validation`, `/api/validation`, `/admin/revalidate`) and a seeded 1CRN reference demo exercise it end-to-end.

**Tech Stack:** Python 3.13, numpy (already a dep), FastAPI + Jinja2, pytest + FastAPI TestClient, Playwright (Chromium, in `.venv`) for screenshot verification.

## Global Constraints

- Pure Python + numpy. No new dependency, no network, no external binaries.
- Test env: `.venv/bin/python -m pytest`. Lint: `ruff check app tests` (ruff is on PATH, not in `.venv`).
- Validation is best-effort: a parse failure on one output stores `{status:"error", reason}` and never aborts seed/ingest/batch.
- Language discipline: "MolProbity-style" / "approximate Ramachandran". NEVER write "MolProbity" as the method name.
- Validation must NOT appear on the blind A/B arena vote screen — only on `/validation`.
- Metrics stored in `model_output.meta_json` under key `"validation"` = `{"self": {...}, "reference": {...}?}`.
- ruff PostToolUse formatter can strip imports added before first use — add import + first use in the SAME edit, then re-grep.
- PDB column spec (0-indexed Python slices): record `[0:6]`, atom serial `[6:11]`, name `[12:16]`, resName `[17:20]`, resSeq `[22:26]`, x `[30:38]`, y `[38:46]`, z `[46:54]`, element `[76:78]`. Real bundled file: `app/data/benchmarks/assets/1crn.pdb` = 327 ATOM records, 46 residues/CA, element right-justified in cols 77-78.
- SDF V2000: line index 3 = counts (`atoms=int(line[0:3])`, `bonds=int(line[3:6])`); atom lines from index 4: `x=[0:10] y=[10:20] z=[20:30] element=line[31:34].strip()`; bond lines follow: `a=int(line[0:3]) b=int(line[3:6])` (1-indexed).

---

### Task 1: Validation core — atom parser + reference-free stereochemistry

**Files:**

- Create: `app/validation.py`
- Test: `tests/test_validation.py`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `class ValidationError(Exception)`
  - `Atom = namedtuple("Atom", "element name resn resi xyz")` (xyz = numpy array shape (3,))
  - `parse_atoms(text: str, fmt: str) -> list[Atom]` — raises `ValidationError` on empty/unparseable.
  - `validate_structure(text: str, fmt: str) -> dict` — `{status, n_atoms, clashes_per_1000, bond_outliers, rama_outliers (int|None), validity_score (0-100), tier ('clean'|'minor'|'major')}`.
  - `MOLECULAR = {"pdb","cif","mmcif","ent","sdf","mol"}`
  - Module tables: `VDW = {...}`, `IDEAL_BOND = {...}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_validation.py
from __future__ import annotations

import numpy as np

from app import validation

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
    import pytest

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_validation.py -q`
Expected: FAIL — `module 'app.validation' has no attribute ...`.

- [ ] **Step 3: Implement `app/validation.py` (parser + stereochemistry)**

```python
"""Objective structure validation — reference-free stereochemistry + reference-based
similarity for molecular outputs (PDB/SDF). Pure Python + numpy, no IO, no network.

This is a lightweight, MolProbity-STYLE validator (NOT the MolProbity binary): the
Ramachandran check uses coarse allowed-region boxes and is labeled approximate.
"""

from __future__ import annotations

from collections import namedtuple

import numpy as np

MOLECULAR = {"pdb", "cif", "mmcif", "ent", "sdf", "mol"}

VDW = {"H": 1.10, "C": 1.70, "N": 1.55, "O": 1.52, "P": 1.80, "S": 1.80}
VDW_DEFAULT = 1.70
IDEAL_BOND = {("C", "C"): 1.54, ("C", "N"): 1.47, ("C", "O"): 1.43, ("C", "H"): 1.09}
IDEAL_DEFAULT = 1.50
BOND_SIGMA = 0.10
CLASH_TOL = 0.40  # Å: clash if dist < vdw_a + vdw_b - CLASH_TOL


class ValidationError(Exception):
    """Raised when a structure cannot be parsed into atoms."""


Atom = namedtuple("Atom", "element name resn resi xyz")
Bond = namedtuple("Bond", "i j")  # 0-indexed atom indices


def _vdw(el):
    return VDW.get(el, VDW_DEFAULT)


def _ideal_bond(a, b):
    return IDEAL_BOND.get((a, b)) or IDEAL_BOND.get((b, a)) or IDEAL_DEFAULT


def _parse_pdb(text):
    atoms, bonds = [], []
    conect = []
    serial_to_idx = {}
    for line in text.splitlines():
        rec = line[:6].strip()
        if rec in ("ATOM", "HETATM"):
            try:
                serial = int(line[6:11])
                name = line[12:16].strip()
                resn = line[17:20].strip()
                resi = int(line[22:26])
                x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
            except ValueError:
                continue
            el = line[76:78].strip() or "".join(c for c in name if c.isalpha())[:1]
            serial_to_idx[serial] = len(atoms)
            atoms.append(Atom(el.capitalize(), name, resn, resi, np.array([x, y, z])))
        elif rec == "CONECT":
            nums = [line[i : i + 5] for i in range(6, len(line.rstrip()), 5)]
            try:
                ser = [int(n) for n in nums if n.strip()]
            except ValueError:
                continue
            for partner in ser[1:]:
                conect.append((ser[0], partner))
    for a, b in conect:
        if a in serial_to_idx and b in serial_to_idx:
            i, j = serial_to_idx[a], serial_to_idx[b]
            if i < j:
                bonds.append(Bond(i, j))
    if not atoms:
        raise ValidationError("no ATOM/HETATM records")
    return atoms, bonds


def _parse_sdf(text):
    lines = text.splitlines()
    if len(lines) < 4:
        raise ValidationError("SDF too short")
    counts = lines[3]
    try:
        n_atoms = int(counts[0:3])
        n_bonds = int(counts[3:6])
    except ValueError:
        raise ValidationError("bad V2000 counts line")
    atoms, bonds = [], []
    for k in range(4, 4 + n_atoms):
        line = lines[k]
        x, y, z = float(line[0:10]), float(line[10:20]), float(line[20:30])
        el = line[31:34].strip()
        atoms.append(Atom(el.capitalize(), el, "LIG", 1, np.array([x, y, z])))
    for k in range(4 + n_atoms, 4 + n_atoms + n_bonds):
        line = lines[k]
        a, b = int(line[0:3]) - 1, int(line[3:6]) - 1
        if a < b:
            bonds.append(Bond(a, b))
        else:
            bonds.append(Bond(b, a))
    if not atoms:
        raise ValidationError("no atoms in SDF")
    return atoms, bonds


def _parse(text, fmt):
    fmt = fmt.lower()
    if fmt in ("sdf", "mol"):
        return _parse_sdf(text)
    return _parse_pdb(text)  # pdb/cif/mmcif/ent treated as PDB records


def parse_atoms(text, fmt):
    """Public: return just the atom list (raises ValidationError if none)."""
    atoms, _ = _parse(text, fmt)
    return atoms


def _clashscore(atoms, bonds):
    bonded = set()
    for bd in bonds:
        bonded.add((bd.i, bd.j))
    coords = np.array([a.xyz for a in atoms])
    n = len(atoms)
    n_clash = 0
    for i in range(n):
        for j in range(i + 1, n):
            if (i, j) in bonded:
                continue
            # skip 1-2 sequential neighbours in a CONECT-less chain (adjacent serials)
            if not bonds and abs(i - j) == 1 and atoms[i].resi == atoms[j].resi:
                continue
            d = float(np.linalg.norm(coords[i] - coords[j]))
            if d < _vdw(atoms[i].element) + _vdw(atoms[j].element) - CLASH_TOL:
                n_clash += 1
    return 1000.0 * n_clash / n if n else 0.0


def _bond_outliers(atoms, bonds):
    bad = 0
    for bd in bonds:
        d = float(np.linalg.norm(atoms[bd.i].xyz - atoms[bd.j].xyz))
        ideal = _ideal_bond(atoms[bd.i].element, atoms[bd.j].element)
        if abs(d - ideal) / BOND_SIGMA > 4.0:
            bad += 1
    return bad


def _dihedral(p0, p1, p2, p3):
    b0, b1, b2 = p0 - p1, p2 - p1, p3 - p2
    b1n = b1 / (np.linalg.norm(b1) + 1e-9)
    v = b0 - np.dot(b0, b1n) * b1n
    w = b2 - np.dot(b2, b1n) * b1n
    x = np.dot(v, w)
    y = np.dot(np.cross(b1n, v), w)
    return float(np.degrees(np.arctan2(y, x)))


# Coarse allowed (phi, psi) boxes — APPROXIMATE, not MolProbity contours.
_RAMA_BOXES = [
    (-160, -20, -120, 50),   # right-handed alpha / general
    (-180, -40, 90, 180),    # beta sheet (upper)
    (-180, -40, -180, -150), # beta sheet (lower wrap)
    (20, 90, -20, 90),       # left-handed alpha
]


def _in_rama(phi, psi):
    for plo, phi_hi, slo, shi in _RAMA_BOXES:
        if plo <= phi <= phi_hi and slo <= psi <= shi:
            return True
    return False


def _rama_outliers(atoms):
    # group backbone atoms by residue
    by_res = {}
    for a in atoms:
        if a.name in ("N", "CA", "C"):
            by_res.setdefault(a.resi, {})[a.name] = a.xyz
    resis = sorted(by_res)
    if len(resis) < 3:
        return None  # not a protein chain
    outliers = 0
    counted = 0
    for k in range(1, len(resis) - 1):
        prev, cur, nxt = by_res[resis[k - 1]], by_res[resis[k]], by_res[resis[k + 1]]
        if not all(x in cur for x in ("N", "CA", "C")):
            continue
        if "C" not in prev or "N" not in nxt:
            continue
        phi = _dihedral(prev["C"], cur["N"], cur["CA"], cur["C"])
        psi = _dihedral(cur["N"], cur["CA"], cur["C"], nxt["N"])
        counted += 1
        if not _in_rama(phi, psi):
            outliers += 1
    return outliers if counted else None


def _score_and_tier(clash, bond_out, rama_out):
    penalty = min(60.0, 6.0 * clash) + min(25.0, 5.0 * bond_out)
    if rama_out:
        penalty += min(15.0, 3.0 * rama_out)
    score = max(0.0, 100.0 - penalty)
    tier = "clean" if score >= 90 else ("minor" if score >= 70 else "major")
    return round(score, 1), tier


def validate_structure(text, fmt):
    atoms, bonds = _parse(text, fmt)
    clash = _clashscore(atoms, bonds)
    bond_out = _bond_outliers(atoms, bonds)
    rama_out = _rama_outliers(atoms)
    score, tier = _score_and_tier(clash, bond_out, rama_out)
    return {
        "status": "ok",
        "n_atoms": len(atoms),
        "clashes_per_1000": round(clash, 2),
        "bond_outliers": int(bond_out),
        "rama_outliers": rama_out,
        "validity_score": score,
        "tier": tier,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_validation.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Real-execution check on the bundled real 1CRN**

```python
# append to tests/test_validation.py
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent / "app" / "data" / "benchmarks" / "assets"


def test_real_1crn_parses_and_scores_sane():
    text = (BENCH / "1crn.pdb").read_text()
    r = validation.validate_structure(text, "pdb")
    assert r["status"] == "ok"
    assert r["n_atoms"] == 327  # real crambin entry
    # a real 1.5 Å crystal structure must not read as garbage
    assert r["tier"] in ("clean", "minor"), r
    assert r["rama_outliers"] is not None  # it IS a protein chain
```

Run: `.venv/bin/python -m pytest tests/test_validation.py::test_real_1crn_parses_and_scores_sane -v`
Expected: PASS. (If it fails on tier, inspect `r` — a column-offset parser bug is the likely cause; do NOT loosen the assertion without understanding why.)

- [ ] **Step 6: Commit**

```bash
git add app/validation.py tests/test_validation.py
git commit -m "feat(validation): atom parser + reference-free stereochemistry (clash/bond/rama)"
```

---

### Task 2: Reference-based similarity — Kabsch RMSD + TM-score + dispatch + perturb util

**Files:**

- Modify: `app/validation.py` (append `compare_to_reference`, `validate_output`, `perturb_pdb`)
- Test: `tests/test_validation.py` (extend)

**Interfaces:**

- Consumes: `parse_atoms`, `Atom`, `MOLECULAR` from Task 1.
- Produces:
  - `compare_to_reference(out_text, ref_text, fmt) -> dict` — `{status:"ok", n_atoms, rmsd, tm_score}` or `{status:"n/a", reason}`.
  - `validate_output(text, fmt) -> dict` — dispatch: molecular → `validate_structure`; else `{status:"n/a", reason:"non-molecular asset"}`; parse error → `{status:"error", reason}`.
  - `perturb_pdb(text, sigma, seed) -> str` — rewrite ATOM/HETATM x/y/z columns with Gaussian jitter; used by seed + this task's real-exec test.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_validation.py
def _compare_coords(atoms):
    return np.array([a.xyz for a in atoms if a.name == "CA"]) if any(
        a.name == "CA" for a in atoms
    ) else np.array([a.xyz for a in atoms])


def test_tm_and_rmsd_identical_is_perfect():
    text = (BENCH / "1crn.pdb").read_text()
    r = validation.compare_to_reference(text, text, "pdb")
    assert r["status"] == "ok"
    assert r["rmsd"] < 1e-6
    assert abs(r["tm_score"] - 1.0) < 1e-6


def test_rmsd_invariant_to_rigid_motion():
    text = (BENCH / "1crn.pdb").read_text()
    moved = validation.perturb_pdb(text, sigma=0.0, seed=1)  # no jitter, but reparse path
    r = validation.compare_to_reference(moved, text, "pdb")
    assert r["rmsd"] < 1e-6


def test_length_mismatch_is_na():
    text = (BENCH / "1crn.pdb").read_text()
    short = "\n".join(text.splitlines()[:10]) + "\n"
    r = validation.compare_to_reference(short, text, "pdb")
    assert r["status"] == "n/a"


def test_perturbation_ordering_near_beats_far():
    text = (BENCH / "1crn.pdb").read_text()
    near = validation.compare_to_reference(validation.perturb_pdb(text, 0.3, 7), text, "pdb")
    far = validation.compare_to_reference(validation.perturb_pdb(text, 2.5, 7), text, "pdb")
    assert near["rmsd"] < far["rmsd"]
    assert near["tm_score"] > far["tm_score"]
    assert near["rmsd"] < 1.0  # σ=0.3 jitter stays sub-Å


def test_validate_output_glb_is_na():
    r = validation.validate_output("glTF binary stub", "glb")
    assert r["status"] == "n/a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_validation.py -q -k "tm or rmsd or mismatch or perturb or validate_output"`
Expected: FAIL — attributes not defined.

- [ ] **Step 3: Append implementation to `app/validation.py`**

```python
def _ca_or_heavy(atoms):
    cas = [a for a in atoms if a.name == "CA"]
    pool = cas if cas else [a for a in atoms if a.element != "H"]
    return np.array([a.xyz for a in pool])


def _kabsch_rmsd_and_dists(P, Q):
    # superpose P onto Q (same length, ordered); return rmsd + per-point distances.
    Pc, Qc = P - P.mean(0), Q - Q.mean(0)
    H = Pc.T @ Qc
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    Pr = Pc @ R.T
    diff = Pr - Qc
    per = np.linalg.norm(diff, axis=1)
    rmsd = float(np.sqrt((per**2).mean()))
    return rmsd, per


def _tm_score(per_dists, length):
    if length < 15:
        d0 = 0.5
    else:
        d0 = max(0.5, 1.24 * (length - 15) ** (1.0 / 3.0) - 1.8)
    return float(np.mean(1.0 / (1.0 + (per_dists / d0) ** 2)))


def compare_to_reference(out_text, ref_text, fmt):
    out_atoms = parse_atoms(out_text, fmt)
    ref_atoms = parse_atoms(ref_text, fmt)
    P, Q = _ca_or_heavy(out_atoms), _ca_or_heavy(ref_atoms)
    if len(P) == 0 or len(Q) == 0 or len(P) != len(Q):
        return {
            "status": "n/a",
            "reason": f"length mismatch (out={len(P)}, ref={len(Q)}) — "
            "sequence-independent alignment not implemented",
        }
    rmsd, per = _kabsch_rmsd_and_dists(P, Q)
    return {
        "status": "ok",
        "n_atoms": int(len(P)),
        "rmsd": round(rmsd, 3),
        "tm_score": round(_tm_score(per, len(P)), 4),
    }


def validate_output(text, fmt):
    fmt = (fmt or "").lower()
    if fmt not in MOLECULAR:
        return {"status": "n/a", "reason": "non-molecular asset"}
    try:
        return validate_structure(text, fmt)
    except ValidationError as e:
        return {"status": "error", "reason": str(e)}


def perturb_pdb(text, sigma, seed):
    """Rewrite ATOM/HETATM x/y/z with Gaussian jitter (σ Å). Used to build demo outputs."""
    rng = np.random.default_rng(seed)
    out = []
    for line in text.splitlines():
        if line[:6].strip() in ("ATOM", "HETATM"):
            try:
                x = float(line[30:38]) + rng.normal(0, sigma)
                y = float(line[38:46]) + rng.normal(0, sigma)
                z = float(line[46:54]) + rng.normal(0, sigma)
                line = f"{line[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{line[54:]}"
            except ValueError:
                pass
        out.append(line)
    return "\n".join(out) + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_validation.py -q`
Expected: PASS (all, ~13).

- [ ] **Step 5: Commit**

```bash
git add app/validation.py tests/test_validation.py
git commit -m "feat(validation): Kabsch RMSD + TM-score reference comparison + perturb util"
```

---

### Task 3: DB wiring — validation_service, /admin/revalidate, seed demo, submission hook

**Files:**

- Create: `app/validation_service.py`
- Modify: `app/main.py` (add `POST /admin/revalidate`)
- Modify: `app/seed.py` (validate molecular outputs; seed 1CRN reference demo)
- Modify: `app/submissions.py` (validate on approval, best-effort)
- Modify: `app/templates/admin.html` (Revalidate button)
- Test: `tests/test_validation_service.py`

**Interfaces:**

- Consumes: `validate_output`, `compare_to_reference`, `perturb_pdb` (Task 2); `get_storage()` (`app/storage.py`); models `ModelOutput`, `Task`.
- Produces:
  - `validation_service.validate_and_store(db, output) -> dict` — reads bytes, computes `self` (+ `reference` if the output's task has a different `reference_asset_id`), merges into `meta_json["validation"]`, flush, returns the validation dict.
  - `validation_service.revalidate_all(db) -> dict` — `{outputs, molecular, errors}` over non-gold outputs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_validation_service.py
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_revalidate_requires_token():
    assert client.post("/admin/revalidate", data={"token": "wrong"}).status_code == 401


def test_revalidate_populates_molecular_output_meta():
    resp = client.post("/admin/revalidate", data={"token": "test-token"})
    assert resp.status_code == 200
    detail = resp.json()["detail"]
    assert detail["molecular"] >= 1  # at least the 1CRN / ligand structures
    assert detail["outputs"] >= detail["molecular"]


def test_reference_demo_produces_rmsd_and_tm():
    client.post("/admin/revalidate", data={"token": "test-token"})
    from app.database import SessionLocal
    from app.models import ModelOutput, Task

    db = SessionLocal()
    try:
        # the crambin-fold task gets a reference + perturbed near/far outputs in seed
        task = db.query(Task).filter(Task.reference_asset_id.isnot(None)).first()
        assert task is not None, "expected a seeded reference task"
        outs = db.query(ModelOutput).filter(ModelOutput.task_id == task.id).all()
        refs = [
            json.loads(o.meta_json).get("validation", {}).get("reference")
            for o in outs
        ]
        ok = [r for r in refs if r and r.get("status") == "ok"]
        assert ok, "expected at least one reference comparison with rmsd/tm"
        assert all("rmsd" in r and "tm_score" in r for r in ok)
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_validation_service.py -q`
Expected: FAIL — `/admin/revalidate` 404 / route missing.

- [ ] **Step 3: Create `app/validation_service.py`**

```python
"""DB wiring for the structure-validation track: compute metrics from stored asset
bytes and cache them in model_output.meta_json["validation"]. Best-effort — a bad
asset stores an error stub and never aborts the batch."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import validation
from .models import ModelOutput
from .storage import get_storage


def _read_text(output: ModelOutput) -> str:
    return get_storage().read(output.asset_path).decode("utf-8", errors="replace")


def validate_and_store(db: Session, output: ModelOutput) -> dict:
    """Compute self (+ reference if applicable) validation and merge into meta_json."""
    try:
        text = _read_text(output)
        self_res = validation.validate_output(text, output.asset_format)
    except Exception as e:  # noqa: BLE001 — best-effort; capture and continue
        self_res = {"status": "error", "reason": str(e)}
    result = {"self": self_res}

    task = output.task
    ref_id = getattr(task, "reference_asset_id", None)
    if ref_id and ref_id != output.id and output.asset_format in validation.MOLECULAR:
        ref = db.get(ModelOutput, ref_id)
        if ref is not None and ref.asset_format == output.asset_format:
            try:
                ref_text = _read_text(ref)
                result["reference"] = validation.compare_to_reference(
                    _read_text(output), ref_text, output.asset_format
                )
            except Exception as e:  # noqa: BLE001
                result["reference"] = {"status": "error", "reason": str(e)}

    meta = json.loads(output.meta_json or "{}")
    meta["validation"] = result
    output.meta_json = json.dumps(meta)
    db.flush()
    return result


def revalidate_all(db: Session) -> dict:
    outputs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    molecular = errors = 0
    for o in outputs:
        res = validate_and_store(db, o)
        if res["self"].get("status") == "ok":
            molecular += 1
        elif res["self"].get("status") == "error":
            errors += 1
    db.commit()
    return {"outputs": len(outputs), "molecular": molecular, "errors": errors}
```

- [ ] **Step 4: Add `POST /admin/revalidate` to `app/main.py`**

Find the `admin_recompute` route (search `@app.post("/admin/recompute")`) and add directly after it:

```python
@app.post("/admin/revalidate")
def admin_revalidate(token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    from . import validation_service

    detail = validation_service.revalidate_all(db)
    return JSONResponse({"status": "revalidated", "detail": detail})
```

- [ ] **Step 5: Seed the 1CRN reference demo in `app/seed.py`**

After the `load_benchmarks(...)` call and BEFORE `db.commit()` (search `load_benchmarks(db,`), add a reference-demo helper call. First add this function near the bottom of `app/seed.py` (module level):

```python
def _seed_reference_demo(db):
    """Wire the benchmark crambin-fold task as a reference + add perturbed near/far
    generator outputs, so the reference-based validation path has real numbers."""
    from pathlib import Path

    from . import config, validation
    from .models import Generator, ModelOutput, Task

    # _publish and get_storage are module-scoped in seed.py (verified at seed.py:36) —
    # _seed_reference_demo lives in the same module, so call _publish(rel) directly.

    task = db.execute(
        select(Task).where(Task.title.like("Crambin%"))
    ).scalars().first()
    if task is None:
        return 0
    ref_out = (
        db.execute(select(ModelOutput).where(ModelOutput.task_id == task.id))
        .scalars()
        .first()
    )
    if ref_out is None:
        return 0
    task.reference_asset_id = ref_out.id
    ref_text = get_storage().read(ref_out.asset_path).decode("utf-8", errors="replace")

    added = 0
    for gslug, gname, sigma in [
        ("predictor-near", "Predictor (near)", 0.3),
        ("predictor-far", "Predictor (far)", 2.5),
    ]:
        gen = db.execute(select(Generator).where(Generator.slug == gslug)).scalars().first()
        if gen is None:
            gen = Generator(slug=gslug, name=gname, kind="model", is_anonymous=True)
            db.add(gen)
            db.flush()
        text = validation.perturb_pdb(ref_text, sigma, seed=hash(gslug) % 100000)
        rel = Path("seed") / f"crambin__{gslug}.pdb"
        (config.ASSET_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
        (config.ASSET_DIR / rel).write_text(text)
        _publish(rel)
        db.add(
            ModelOutput(
                task_id=task.id,
                generator_id=gen.id,
                title=f"{task.title} — {gname}",
                asset_path=str(rel).replace("\\", "/"),
                asset_format="pdb",
                meta_json=json.dumps({"generator": gslug, "perturb_sigma": sigma}),
            )
        )
        added += 1
    db.flush()
    return added
```

Then, immediately before the final `db.commit()` in `seed_all`, call it and validate everything:

```python
        _seed_reference_demo(db)
        from . import validation_service

        for out in db.execute(select(ModelOutput)).scalars().all():
            validation_service.validate_and_store(db, out)
```

(If `_publish` is a closure inside `seed_all` rather than module-scoped, inline its body: `get_storage().save(str(rel), (config.ASSET_DIR / rel).read_bytes())` instead of importing it.)

- [ ] **Step 6: Validate on submission approval in `app/submissions.py`**

In `approve(...)`, after the `ModelOutput(...)` is added and flushed (search `output = ModelOutput(`), add best-effort validation:

```python
    db.flush()  # ensure output.id + relationships
    try:
        from . import validation_service

        validation_service.validate_and_store(db, output)
    except Exception:  # noqa: BLE001 — approval must not fail on a bad asset
        pass
```

- [ ] **Step 7: Add a Revalidate button to `app/templates/admin.html`**

Find the form posting to `/admin/recompute` and add a sibling card/form after it:

```html
<form method="post" action="/admin/revalidate" class="card">
  <h3>Revalidate structures</h3>
  <p class="subtle">
    Recompute objective structure-validation metrics for all molecular outputs.
  </p>
  <input type="hidden" name="token" class="token-mirror" />
  <button type="submit">Revalidate</button>
</form>
```

(Verified: admin.html uses a single `#admin-token` input mirrored by JS into each form's
`<input type="hidden" name="token" class="token-mirror" />` — the button above matches that
pattern. No `{{ token }}` server var exists.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_validation_service.py -q`
Expected: PASS (3 passed).

- [ ] **Step 9: Run the full suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add app/validation_service.py app/main.py app/seed.py app/submissions.py app/templates/admin.html tests/test_validation_service.py
git commit -m "feat(validation): db wiring, /admin/revalidate, 1CRN reference demo, approval hook"
```

---

### Task 4: `/validation` page + `/api/validation` + nav link

**Files:**

- Modify: `app/validation_service.py` (add aggregations)
- Modify: `app/main.py` (`GET /validation`, `GET /api/validation`)
- Create: `app/templates/validation.html`
- Modify: `app/templates/base.html` (nav link)
- Test: `tests/test_validation_service.py` (extend)

**Interfaces:**

- Consumes: meta_json["validation"] populated by Task 3.
- Produces:
  - `validation_service.validity_leaderboard(db) -> list[dict]` — per-generator: `{generator, n_molecular, mean_validity, mean_clashes, clean, minor, major}`, sorted by mean_validity desc, generators with `n_molecular==0` excluded.
  - `validation_service.reference_accuracy(db) -> list[dict]` — per task with a reference: `{task, reference_generator, rows:[{generator, rmsd, tm_score}]}` sorted by tm_score desc.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_validation_service.py
def test_validation_page_and_api_render():
    client.post("/admin/revalidate", data={"token": "test-token"})
    html = client.get("/validation").text
    assert "Physical validity" in html
    assert "independent of the human aesthetic vote" in html
    api = client.get("/api/validation").json()
    assert "validity_leaderboard" in api
    assert any(row["n_molecular"] >= 1 for row in api["validity_leaderboard"])
    # reference section present because the crambin demo has a reference
    assert api["reference_accuracy"], "expected reference accuracy rows"


def test_validation_in_nav():
    html = client.get("/").text
    nav = html.split("<nav>")[1].split("</nav>")[0]
    assert ">Validation<" in nav
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_validation_service.py -q -k "page or nav"`
Expected: FAIL — `/validation` 404, no nav link.

- [ ] **Step 3: Add aggregations to `app/validation_service.py`**

```python
def _gen_name(db, gid):
    from .models import Generator

    g = db.get(Generator, gid)
    return g.name if g else str(gid)


def validity_leaderboard(db: Session) -> list[dict]:
    from collections import defaultdict

    acc = defaultdict(lambda: {"scores": [], "clashes": [], "clean": 0, "minor": 0, "major": 0})
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    for o in outs:
        v = json.loads(o.meta_json or "{}").get("validation", {}).get("self", {})
        if v.get("status") != "ok":
            continue
        a = acc[o.generator_id]
        a["scores"].append(v["validity_score"])
        a["clashes"].append(v["clashes_per_1000"])
        a[v["tier"]] += 1
    rows = []
    for gid, a in acc.items():
        n = len(a["scores"])
        if n == 0:
            continue
        rows.append(
            {
                "generator": _gen_name(db, gid),
                "n_molecular": n,
                "mean_validity": round(sum(a["scores"]) / n, 1),
                "mean_clashes": round(sum(a["clashes"]) / n, 2),
                "clean": a["clean"],
                "minor": a["minor"],
                "major": a["major"],
            }
        )
    rows.sort(key=lambda r: r["mean_validity"], reverse=True)
    return rows


def reference_accuracy(db: Session) -> list[dict]:
    from .models import Task

    tasks = db.execute(select(Task).where(Task.reference_asset_id.isnot(None))).scalars().all()
    out = []
    for t in tasks:
        ref = db.get(ModelOutput, t.reference_asset_id)
        ref_gen = _gen_name(db, ref.generator_id) if ref else "—"
        rows = []
        for o in db.execute(select(ModelOutput).where(ModelOutput.task_id == t.id)).scalars().all():
            r = json.loads(o.meta_json or "{}").get("validation", {}).get("reference")
            if r and r.get("status") == "ok":
                rows.append(
                    {
                        "generator": _gen_name(db, o.generator_id),
                        "rmsd": r["rmsd"],
                        "tm_score": r["tm_score"],
                    }
                )
        if rows:
            rows.sort(key=lambda x: x["tm_score"], reverse=True)
            out.append({"task": t.title, "reference_generator": ref_gen, "rows": rows})
    return out
```

- [ ] **Step 4: Add routes to `app/main.py`**

Near the other page routes (e.g. after the `/significance` route), add:

```python
@app.get("/validation", response_class=HTMLResponse)
def validation_page(request: Request, db: Session = Depends(get_db)):
    from . import validation_service

    return templates.TemplateResponse(
        "validation.html",
        {
            "request": request,
            "validity": validation_service.validity_leaderboard(db),
            "reference": validation_service.reference_accuracy(db),
        },
    )


@app.get("/api/validation")
def api_validation(db: Session = Depends(get_db)):
    from . import validation_service

    return JSONResponse(
        {
            "validity_leaderboard": validation_service.validity_leaderboard(db),
            "reference_accuracy": validation_service.reference_accuracy(db),
        }
    )
```

- [ ] **Step 5: Create `app/templates/validation.html`**

```html
{% extends "base.html" %} {% block title %}Validation · Bio 3D Arena{% endblock
%} {% block content %}
<section class="board">
  <h2>
    Structure validation <span class="subtle">— objective, computed</span>
  </h2>
  <p class="subtle">
    Objective structure checks, computed from the geometry —
    <b>independent of the human aesthetic vote</b>. Molecular outputs only
    (PDB/SDF); mesh assets are not scored. Reference-free metrics are
    MolProbity-style approximations (coarse Ramachandran); similarity requires
    equal-length atom correspondence.
  </p>

  <h3>Physical validity</h3>
  {% if validity %}
  <table class="ranktable">
    <thead>
      <tr>
        <th>#</th>
        <th>Generator</th>
        <th>Mean validity</th>
        <th>Mean clashes/1k</th>
        <th>Clean</th>
        <th>Minor</th>
        <th>Major</th>
        <th>n</th>
      </tr>
    </thead>
    <tbody>
      {% for r in validity %}
      <tr>
        <td>{{ loop.index }}</td>
        <td class="gen">{{ r.generator }}</td>
        <td class="num strong">{{ r.mean_validity }}</td>
        <td class="num">{{ r.mean_clashes }}</td>
        <td class="num">{{ r.clean }}</td>
        <td class="num">{{ r.minor }}</td>
        <td class="num">{{ r.major }}</td>
        <td class="num">{{ r.n_molecular }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p>No molecular outputs scored yet. Run <b>Revalidate</b> in Admin.</p>
  {% endif %} {% if reference %}
  <h3>
    Reference-based accuracy
    <span class="subtle">— vs ground-truth structure</span>
  </h3>
  {% for t in reference %}
  <p class="subtle">
    <b>{{ t.task }}</b> · reference: {{ t.reference_generator }}
  </p>
  <table class="ranktable">
    <thead>
      <tr>
        <th>Generator</th>
        <th>TM-score</th>
        <th>RMSD (Å)</th>
      </tr>
    </thead>
    <tbody>
      {% for row in t.rows %}
      <tr>
        <td class="gen">{{ row.generator }}</td>
        <td class="num strong">{{ row.tm_score }}</td>
        <td class="num">{{ row.rmsd }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endfor %} {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 6: Add the nav link in `app/templates/base.html`**

In the `<nav>` block, after the Significance link, add:

```html
<a href="/validation">Validation</a>
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_validation_service.py -q`
Expected: PASS.

- [ ] **Step 8: Screenshot-verify the page**

Write `/home/mjarnold/.claude/jobs/3400ad8a/tmp/shoot_validation.py` (model on the existing `shoot.py`: set `DATABASE_URL` to a throwaway DB, `seed_all(force=True)`, POST `/admin/revalidate` with token `test-token`, screenshot `/validation` full-page). Run:
`PYTHONPATH=$(pwd) .venv/bin/python /home/mjarnold/.claude/jobs/3400ad8a/tmp/shoot_validation.py`
Then Read the PNG and confirm the validity leaderboard + reference table render with real numbers (near > far TM-score).

- [ ] **Step 9: Run full suite + ruff**

Run: `.venv/bin/python -m pytest -q && ruff check app tests`
Expected: all pass, ruff clean.

- [ ] **Step 10: Commit**

```bash
git add app/validation_service.py app/main.py app/templates/validation.html app/templates/base.html tests/test_validation_service.py
git commit -m "feat(validation): /validation page + /api/validation + nav link"
```

---

## Final verification (controller, before merge)

- [ ] Full suite ≥2× green (pre-existing flakes surface at merge time — Inc2 lesson): `.venv/bin/python -m pytest -q` twice.
- [ ] ruff clean: `ruff check app tests`.
- [ ] Screenshot `/validation` and the arena (confirm validation did NOT leak into the blind vote screen).
- [ ] Independent code review of `git diff master..HEAD` before ff-merge (Inc3 caught real bugs this way) — focus: parser column offsets, Kabsch correctness, the `_publish`/closure assumption in seed, any validation call path that could crash seed/ingest.
- [ ] Suite-gated ff-merge to master (`pytest && git -C /home/mjarnold/bio3d-arena merge --ff-only worktree-bio3d-arena-mvp`).
- [ ] Update `docs/audits/2026-06-20-field-audit.md` (mark B3 done) + the memory roadmap (#4 done, #5 next).

## Self-review notes (author)

- **Spec coverage:** reference-free (Task 1) ✓; reference-based (Task 2) ✓; storage/meta + /admin/revalidate + demo + approval hook (Task 3) ✓; /validation page + api + nav (Task 4) ✓; real-execution check on 1CRN (Tasks 1-2) ✓; error-handling table (validate_output n/a/error, best-effort store) ✓.
- **Resolved (grounded at plan time):** `app/seed.py`'s `_publish` IS module-scoped (seed.py:36) →
  `_seed_reference_demo` calls it directly. `admin.html` uses the JS `.token-mirror` pattern (no
  `{{ token }}` server var) → the Revalidate button matches it. Both Step 5 and Step 7 reflect this.
- **Still read-before-edit at execution (Iron Law):** the exact insertion points in `seed.py`
  (`load_benchmarks(db,` call site, the final `db.commit()`) and `submissions.py` (`output =
ModelOutput(`) — confirm current line context before editing.
