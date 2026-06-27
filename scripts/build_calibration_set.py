"""CLI: build the shared calibration subset. Usage:
.venv/bin/python scripts/build_calibration_set.py --n 50 --seed 12345"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calibration import build_calibration_set  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=50, help="pairs per criterion")
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args()
    with SessionLocal() as db:
        res = build_calibration_set(db, n_per_criterion=args.n, seed=args.seed)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
