"""Pytest bootstrap: isolate the test DB + assets into a temp dir before app import."""

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="bio3d_test_"))
os.environ.setdefault("BIO3D_DATA_DIR", str(_tmp))
os.environ.setdefault("BIO3D_ADMIN_TOKEN", "test-token")
os.environ.setdefault("BIO3D_BT_BOOTSTRAP", "20")  # keep bootstrap cheap in tests
