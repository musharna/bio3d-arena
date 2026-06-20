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
IDEAL_BOND = {
    ("C", "C"): 1.54,
    ("C", "N"): 1.47,
    ("C", "O"): 1.43,
    ("C", "H"): 1.09,
    ("C", "S"): 1.81,
    ("S", "S"): 2.04,  # disulfide
    ("O", "P"): 1.61,
}
IDEAL_DEFAULT = 1.50
BOND_SIGMA = 0.10
CLASH_TOL = 0.40  # Å: clash if dist < vdw_a + vdw_b - CLASH_TOL
# Distance-based bond inference (most real PDB files carry no CONECT records, so we
# cannot tell bonds from steric clashes by the VDW test alone). Heavy-atom covalent
# bonds fall in ~0.9–1.9 Å; anything below BOND_LO is an impossible overlap = clash.
BOND_LO = 0.9
BOND_HI = 2.1  # upper covalent bound — includes disulfide S–S (~2.05 Å)
# Atoms separated by <= this many bonds are excluded from clash detection: 1-2/1-3
# are rigid covalent geometry, 1-4 is governed by torsion (not steric clash). This
# is the standard reduced-clash treatment for a heavy-atom-only (no-H) proxy.
CLASH_BOND_SEP = 3


class ValidationError(Exception):
    """Raised when a structure cannot be parsed into atoms."""


Atom = namedtuple("Atom", "element name resn resi xyz")
Bond = namedtuple("Bond", "i j")  # 0-indexed atom indices


def _vdw(el):
    return VDW.get(el, VDW_DEFAULT)


def _ideal_bond(a, b):
    return IDEAL_BOND.get((a, b)) or IDEAL_BOND.get((b, a)) or IDEAL_DEFAULT


def _parse_pdb(text):
    atoms, conect = [], []
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
    bonds = []
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
    except ValueError as e:
        raise ValidationError("bad V2000 counts line") from e
    atoms, bonds = [], []
    for k in range(4, 4 + n_atoms):
        line = lines[k]
        x, y, z = float(line[0:10]), float(line[10:20]), float(line[20:30])
        el = line[31:34].strip()
        atoms.append(Atom(el.capitalize(), el, "LIG", 1, np.array([x, y, z])))
    for k in range(4 + n_atoms, 4 + n_atoms + n_bonds):
        line = lines[k]
        a, b = int(line[0:3]) - 1, int(line[3:6]) - 1
        bonds.append(Bond(a, b) if a < b else Bond(b, a))
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


def _infer_adjacency(atoms, bonds):
    """Union of explicit bonds and distance-inferred covalent bonds (0.9–1.9 Å).

    Returns adj[i] = set of bonded neighbour indices. Used to exclude 1-2 and 1-3
    pairs from clash detection so real covalent geometry doesn't read as clashes.
    """
    from collections import defaultdict

    adj = defaultdict(set)
    for bd in bonds:
        adj[bd.i].add(bd.j)
        adj[bd.j].add(bd.i)
    coords = np.array([a.xyz for a in atoms])
    n = len(atoms)
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(coords[i] - coords[j]))
            if BOND_LO <= d <= BOND_HI:
                adj[i].add(j)
                adj[j].add(i)
    return adj


def _near_in_bonds(adj, n, sep):
    """For each atom, the set of atoms reachable within `sep` bonds (BFS)."""
    near = []
    for start in range(n):
        seen = {start}
        frontier = {start}
        for _ in range(sep):
            nxt = set()
            for a in frontier:
                nxt |= adj[a] - seen
            seen |= nxt
            frontier = nxt
        seen.discard(start)
        near.append(seen)
    return near


def _clashscore(atoms, bonds):
    coords = np.array([a.xyz for a in atoms])
    n = len(atoms)
    if n == 0:
        return 0.0
    adj = _infer_adjacency(atoms, bonds)
    near = _near_in_bonds(adj, n, CLASH_BOND_SEP)
    n_clash = 0
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(coords[i] - coords[j]))
            if d < BOND_LO:
                n_clash += 1  # impossible overlap — clash regardless of connectivity
                continue
            if j in near[i]:
                continue  # 1-2 / 1-3 / 1-4 — not a steric clash
            if d < _vdw(atoms[i].element) + _vdw(atoms[j].element) - CLASH_TOL:
                n_clash += 1
    return 1000.0 * n_clash / n


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
    (-160, -20, -120, 50),  # right-handed alpha / general
    (-180, -40, 90, 180),  # beta sheet (upper)
    (-180, -40, -180, -150),  # beta sheet (lower wrap)
    (20, 90, -20, 90),  # left-handed alpha
]


def _in_rama(phi, psi):
    for plo, phi_hi, slo, shi in _RAMA_BOXES:
        if plo <= phi <= phi_hi and slo <= psi <= shi:
            return True
    return False


def _rama_outliers(atoms):
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


# --- Reference-based similarity (Kabsch RMSD + TM-score) ---------------------


def _ca_or_heavy(atoms):
    """Comparison atoms in order: protein → CA atoms; else all heavy (non-H) atoms."""
    cas = [a for a in atoms if a.name == "CA"]
    pool = cas if cas else [a for a in atoms if a.element != "H"]
    return np.array([a.xyz for a in pool])


def _kabsch_rmsd_and_dists(P, Q):
    """Superpose P onto Q (same length, ordered); return rmsd + per-point distances."""
    Pc, Qc = P - P.mean(0), Q - Q.mean(0)
    H = Pc.T @ Qc
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    Pr = Pc @ R.T
    per = np.linalg.norm(Pr - Qc, axis=1)
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
    """Dispatch entry point: molecular fmt → stereochemistry; else n/a; parse err → error."""
    fmt = (fmt or "").lower()
    if fmt not in MOLECULAR:
        return {"status": "n/a", "reason": "non-molecular asset"}
    try:
        return validate_structure(text, fmt)
    except ValidationError as e:
        return {"status": "error", "reason": str(e)}


def perturb_pdb(text, sigma, seed):
    """Rewrite ATOM/HETATM x/y/z columns with Gaussian jitter (σ Å). Builds demo outputs."""
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
