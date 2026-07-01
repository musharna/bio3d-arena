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

# --- Public-arena integrity / anti-abuse ---
# Rate limiting: at most VOTE_RATE_LIMIT votes per VOTE_RATE_WINDOW seconds per session.
VOTE_RATE_LIMIT = int(os.environ.get("BIO3D_VOTE_RATE_LIMIT", "60"))
VOTE_RATE_WINDOW = float(os.environ.get("BIO3D_VOTE_RATE_WINDOW", "60"))
# Probability of serving a gold-standard attention-check pair instead of a real one.
GOLD_RATE = float(os.environ.get("BIO3D_GOLD_RATE", "0.1"))
# Sessions below this trust score are excluded from the authoritative BT leaderboard.
TRUST_THRESHOLD = float(os.environ.get("BIO3D_TRUST_THRESHOLD", "0.5"))
# Optional human-verification (captcha). Off by default so local/dev needs no keys.
REQUIRE_CAPTCHA = os.environ.get("BIO3D_REQUIRE_CAPTCHA", "false").lower() in ("1", "true", "yes")
CAPTCHA_PROVIDER = os.environ.get("BIO3D_CAPTCHA_PROVIDER", "turnstile").lower()  # turnstile|hcaptcha
CAPTCHA_SECRET = os.environ.get("BIO3D_CAPTCHA_SECRET", "")

# --- Verified login (Hugging Face OAuth). Off unless client id+secret are set. ---
HF_CLIENT_ID = os.environ.get("BIO3D_HF_CLIENT_ID", "")
HF_CLIENT_SECRET = os.environ.get("BIO3D_HF_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("BIO3D_SESSION_SECRET", "dev-session-secret-change-me")
PUBLIC_BASE_URL = os.environ.get("BIO3D_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# --- Scale-out: storage, DB pooling, distributed rate limiting ---
# Asset storage backend: "local" (filesystem + StaticFiles) or "s3" (object store).
STORAGE_BACKEND = os.environ.get("BIO3D_STORAGE_BACKEND", "local").lower()
S3_BUCKET = os.environ.get("BIO3D_S3_BUCKET", "")
S3_PREFIX = os.environ.get("BIO3D_S3_PREFIX", "")
S3_PUBLIC_BASE_URL = os.environ.get("BIO3D_S3_PUBLIC_BASE_URL", "")  # e.g. a CDN domain
S3_PRESIGN_TTL = int(os.environ.get("BIO3D_S3_PRESIGN_TTL", "3600"))

# SQLAlchemy connection pool (ignored for SQLite).
DB_POOL_SIZE = int(os.environ.get("BIO3D_DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.environ.get("BIO3D_DB_MAX_OVERFLOW", "10"))

# Distributed rate limiting: set a redis:// URL to share limits across workers.
REDIS_URL = os.environ.get("BIO3D_REDIS_URL", "")

# Mode-B recon-accuracy scorer (AgriGen's /score microservice). bio3d-arena POSTs GLB
# bytes here for objective chamfer/F-score grading vs held-out GT (never imports agrigen).
RECON_SCORER_URL = os.environ.get("BIO3D_RECON_SCORER_URL", "http://127.0.0.1:8800")

# Public instances run with an empty scorer URL → scoring disabled (scores are promoted,
# never recomputed). Keeps the public deploy free of the Agrigen scoring microservice.
SCORING_ENABLED = bool(RECON_SCORER_URL.strip())

# Held-out GT scan bundle (the scorer's gt_bundle_prod). Read ONCE at build time by
# scripts/render_gt.py to bake per-species reference GLBs into bio3d's own asset store;
# the running server never touches this path (stays decoupled from the scorer's FS).
GT_BUNDLE_DIR = Path(
    os.environ.get("BIO3D_GT_BUNDLE_DIR", "/home/mjarnold/agrigen/backend/data/gt_bundle_prod")
)
# Storage subdir (relative to ASSET_DIR / S3 prefix) for baked GT reference GLBs.
GT_ASSET_SUBDIR = "gt"

# Directory holding built dataset releases (each a <version>/ subdir with VERSION + DATASHEET).
RELEASES_DIR = DATA_DIR / "releases"


def is_safe_test_db_target(value: str | None) -> bool:
    """True if a DB URL/path is a throwaway that's safe for the test suite to drop/recreate.

    The suite wipes tables; pointing it at a real DB destroys data (incident 2026-06-28:
    pytest with BIO3D_DATABASE_URL=study wiped the study DB). Unset (None) is safe — the
    conftest isolates into a temp dir. An explicit value is safe only if it's in-memory or
    clearly under a temp/test path; anything else (study, prod, a working DB) is rejected.
    """
    if not value:
        return True
    low = value.lower()
    return any(marker in low for marker in (":memory:", "/tmp/", "bio3d_test_", "test"))


def ensure_dirs() -> None:
    """Create data + asset directories if missing (idempotent)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
