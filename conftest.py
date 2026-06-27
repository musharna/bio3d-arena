"""Pytest bootstrap: isolate the test DB + assets into a temp dir before app import."""

import os
import shutil
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="bio3d_test_"))
os.environ.setdefault("BIO3D_DATA_DIR", str(_tmp))
os.environ.setdefault("BIO3D_ADMIN_TOKEN", "test-token")
os.environ.setdefault("BIO3D_BT_BOOTSTRAP", "20")  # keep bootstrap cheap in tests
# Disable random gold injection by default so non-integrity tests are deterministic;
# test_integrity.py sets config.GOLD_RATE explicitly where it needs gold checks.
os.environ.setdefault("BIO3D_GOLD_RATE", "0")

# Seed reference photos into the isolated test ASSET_DIR so tests that read them (e.g.
# test_generate_multiview_recon) don't have to skip or set BIO3D_DATA_DIR externally.
# Source: real data/assets/reference/ (gitignored); skip gracefully if absent (CI without data).
_real_ref = Path(__file__).resolve().parent / "data" / "assets" / "reference"
_test_ref = Path(os.environ["BIO3D_DATA_DIR"]) / "assets" / "reference"
_test_ref.mkdir(parents=True, exist_ok=True)
for _slug in ("arabidopsis", "pinus", "tomato", "rose", "soybean"):
    for _ext in (".jpg", ".json"):
        _src = _real_ref / f"{_slug}_ref{_ext}"
        _dst = _test_ref / f"{_slug}_ref{_ext}"
        if _src.exists() and not _dst.exists():
            shutil.copy(_src, _dst)
