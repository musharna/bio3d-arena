"""Procedural molecular-structure generation (PDB) for demo/seed data.

Writes small, valid, visibly-distinct PDB files so the molecular viewer has real
content out of the box. Like assets_gen for meshes — real deployments ingest
actual generator outputs (PDB/mmCIF from structure predictors, etc.).

A connected chain of atoms (~1.5 Å spacing) so 3Dmol auto-detects bonds and
renders ball-and-stick. Element/length vary deterministically by seed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

_ELEMENTS = ["C", "N", "O", "C", "C"]  # carbon-biased, organic-looking


def _pdb_atom_line(serial: int, name: str, resn: str, resi: int, xyz, element: str) -> str:
    # Fixed-column PDB ATOM/HETATM record (cols matter for strict parsers).
    return (
        f"HETATM{serial:>5} {name:<4} {resn:<3} A{resi:>4}    "
        f"{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}  1.00  0.00          {element:>2}"
    )


def build_molecule_pdb(seed: int, out_path: Path) -> dict:
    """Write a small connected molecule as PDB. Returns provenance meta."""
    rng = np.random.default_rng(seed)
    n_atoms = int(rng.integers(6, 14))
    pos = np.zeros(3)
    lines = ["REMARK  Bio 3D Arena procedural demo molecule", "COMPND    LIGAND"]
    coords = []
    for i in range(n_atoms):
        element = _ELEMENTS[int(rng.integers(0, len(_ELEMENTS)))]
        coords.append((i + 1, element, pos.copy()))
        lines.append(_pdb_atom_line(i + 1, f"{element}{i + 1}", "LIG", 1, pos, element))
        # Step ~1.5 Å in a random direction so consecutive atoms bond.
        step = rng.normal(0, 1, 3)
        step = step / (np.linalg.norm(step) + 1e-9) * 1.5
        pos = pos + step
    # Explicit CONECT chain so bonds are unambiguous.
    for i in range(1, n_atoms):
        lines.append(f"CONECT{i:>5}{i + 1:>5}")
    lines.append("END")
    text = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    return {"format": "pdb", "seed": int(seed), "atoms": n_atoms, "generated": True}
