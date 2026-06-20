"""Runtime configuration, read from environment with sensible local defaults."""

from __future__ import annotations

import os
from pathlib import Path

# Project root = parent of the app/ package directory.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BIO3D_DATA_DIR", ROOT / "data"))
ASSET_DIR = DATA_DIR / "assets"
DB_PATH = Path(os.environ.get("BIO3D_DB_PATH", DATA_DIR / "arena.db"))

DATABASE_URL = os.environ.get("BIO3D_DATABASE_URL", f"sqlite:///{DB_PATH}")

# Shared bearer token for the admin UI/endpoints. Change in production.
ADMIN_TOKEN = os.environ.get("BIO3D_ADMIN_TOKEN", "changeme-admin-token")

# Elo K-factor for online updates.
ELO_K = float(os.environ.get("BIO3D_ELO_K", "32"))

# Number of bootstrap resamples for Bradley-Terry confidence intervals.
BT_BOOTSTRAP = int(os.environ.get("BIO3D_BT_BOOTSTRAP", "200"))


def ensure_dirs() -> None:
    """Create data + asset directories if missing (idempotent)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
