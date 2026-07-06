"""Live smoke for the Mode-B organ-fidelity boundary: drive the 5 seed-PD records through
bio3d's real `recon_client.score_structure` against a running AgriGen /score_structure service
and assert each reproduces AgriGen's own verdict (report.json, 2026-06-26).

This is the real-execution counterpart to the fake-scorer unit tests (test_structure_service.py):
those cover the resolve/map/store logic; this confirms the actual HTTP contract + that the
embedded seed records still match the upstream reference.

Run (no GT bundle needed for /score_structure):
    # in agrigen/backend, with its venv:
    AGRIGEN_GT_BUNDLE= .venv/bin/python -m uvicorn agrigen.scoring_service.app:app --port 8077
    # then here:
    BIO3D_RECON_SCORER_URL=http://127.0.0.1:8077 python scripts/smoke_score_structure.py
"""

from __future__ import annotations

import os
import sys

# bootstrap: allow `python scripts/<name>.py` without PYTHONPATH (repo root on sys.path)
import sys as _sys
import pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent))

from app.recon_client import ScorerError, score_structure
from app.structure_records import SEED_PD_RECORDS, seed_record_for_species

# Pre-registered verdicts: the seed PDs for the 5 covered species. The two 0.0s are real,
# documented structural gaps (pine: PD doesn't model needles-per-fascicle; fagus: no leaf axis).
EXPECTED = {
    "zea_mays": 1.0,
    "arabidopsis_thaliana": 1.0,
    "solanum_lycopersicum": 1.0,
    "pinus_sylvestris": 0.0,
    "fagus_sylvatica": 0.0,
}


def main() -> int:
    base_url = os.environ.get("BIO3D_RECON_SCORER_URL", "http://127.0.0.1:8077")
    try:
        ok = True
        for slug in SEED_PD_RECORDS:
            card = score_structure(seed_record_for_species(slug), base_url=base_url)
            bf = card.get("botanical_fidelity")
            match = bf == EXPECTED[slug]
            ok &= match
            print(
                f"  {slug:<24} fidelity={bf!s:<5} expected={EXPECTED[slug]} "
                f"{'OK' if match else 'MISMATCH'}"
            )
        # Un-referenced species → honest N/A (null + note), not an error.
        nr = score_structure({"species": "rosa_canina"}, base_url=base_url)
        print(f"  rosa_canina (uncovered): {nr.get('botanical_fidelity')} note={nr.get('note')!r}")
        ok &= nr.get("botanical_fidelity") is None
    except ScorerError as e:
        print(
            f"service unreachable at {base_url}: {e}\n"
            "Start AgriGen's scoring service first (see this file's docstring).",
            file=sys.stderr,
        )
        return 2
    print("ALL MATCH" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
