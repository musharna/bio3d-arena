"""Download larger open benchmark corpora into app/data/benchmarks/assets/.

Network-gated (run at deploy/curation time, NOT in tests). Extends manifest.json
with fetched entries. Records source/license/attribution per the audit's license
watch. Currently wired for RCSB PDB (CC0) IDs and ligand SDFs; extend SOURCES for
RNA-Puzzles, CAMEO, HuBMAP HRA, etc.

Usage: .venv/bin/python scripts/fetch_benchmarks.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "app" / "data" / "benchmarks" / "assets"
MANIFEST = ROOT / "app" / "data" / "benchmarks" / "manifest.json"

# (pdb_id, category, title, prompt) — RCSB structures are CC0.
PDB_SOURCES = [
    (
        "1UBQ",
        "proteins",
        "Ubiquitin (1UBQ) — real fold reference",
        "Ubiquitin, 76 residues — a benchmark reference fold.",
    ),
]


def _fetch(url: str, dest: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 — trusted RCSB host
            dest.write_bytes(r.read())
        return True
    except Exception as exc:  # noqa: BLE001 — surface the fetch failure, keep going
        print(f"  ! failed {url}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    have = {e["file"] for e in manifest}
    for pdb_id, category, title, prompt in PDB_SOURCES:
        rel = f"assets/{pdb_id.lower()}.pdb"
        if rel in have:
            continue
        if _fetch(
            f"https://files.rcsb.org/download/{pdb_id}.pdb", ASSETS / f"{pdb_id.lower()}.pdb"
        ):
            manifest.append(
                {
                    "task_slug": f"{pdb_id.lower()}-fold",
                    "category": category,
                    "title": title,
                    "prompt": prompt,
                    "generator_slug": "rcsb-experimental",
                    "generator_name": "RCSB experimental",
                    "file": rel,
                    "format": "pdb",
                    "source": f"https://www.rcsb.org/structure/{pdb_id}",
                    "license": "CC0",
                    "attribution": f"RCSB PDB ({pdb_id})",
                }
            )
            print(f"  + {pdb_id}")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest now has {len(manifest)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
