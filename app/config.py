"""Runtime configuration, read from environment with sensible local defaults."""

from __future__ import annotations

import os
from pathlib import Path

from .envfile import load_env_file

# Project root = parent of the app/ package directory.
ROOT = Path(__file__).resolve().parent.parent

# Fold the repo's .env into the environment before anything below reads it. Keys live there
# (OPENROUTER_API_KEY) and no consumer loaded them, so they read as unset; from a worktree the
# gitignored .env is only in the main checkout. The shell always wins over the file, and a .env
# naming a database is fatal — see app/envfile.py.
load_env_file(ROOT)

DATA_DIR = Path(os.environ.get("BIO3D_DATA_DIR", ROOT / "data"))
ASSET_DIR = DATA_DIR / "assets"
DB_PATH = Path(os.environ.get("BIO3D_DB_PATH", DATA_DIR / "arena.db"))

DATABASE_URL = os.environ.get("BIO3D_DATABASE_URL", f"sqlite:///{DB_PATH}")

# Mode-B recon-accuracy scorer (AgriGen's /score microservice). Read HERE rather than beside
# its siblings below because the deploy-safety guards under it need the public/internal signal.
RECON_SCORER_URL = os.environ.get("BIO3D_RECON_SCORER_URL", "http://127.0.0.1:8800")

# One source of truth for "is this the PUBLIC deploy?". The public instance runs with an empty
# scorer URL (scores are promoted, never recomputed), so this is the signal the codebase already
# uses to separate public from internal — see SCORING_ENABLED / INTERNAL_PAGES_ENABLED. Reusing
# it means the security guards below need NO extra knob a deployer could forget to set.
IS_PUBLIC_DEPLOY = not RECON_SCORER_URL.strip()

# Shared bearer token for the admin UI/endpoints (create/modify generators, tasks and outputs;
# trigger recomputes — i.e. full write access to the benchmark).
#
# This used to fall back to the literal below on ANY deploy. The 2026-07-26 pre-launch audit
# confirmed the consequence live: `GET /admin?token=changeme-admin-token` -> 200. A public
# instance that forgot BIO3D_ADMIN_TOKEN was administrable with a token published in the source
# tree. So the public deploy now FAILS LOUD at import instead of silently falling back — and it
# rejects the literal itself, or copy-pasting the documented default would reopen the same hole.
# Internal/local instances keep the convenience default: they bind loopback, and breaking every
# dev run and test would buy nothing.
_DEV_ADMIN_TOKEN = "changeme-admin-token"  # local-only; never valid on a public deploy
_admin_token_env = os.environ.get("BIO3D_ADMIN_TOKEN", "").strip()
if IS_PUBLIC_DEPLOY and _admin_token_env in ("", _DEV_ADMIN_TOKEN):
    raise RuntimeError(
        "Refusing to start a PUBLIC deploy without a real admin token. "
        "Set BIO3D_ADMIN_TOKEN to a secret value (not the local default "
        f"{_DEV_ADMIN_TOKEN!r}). A public deploy is one with an empty "
        "BIO3D_RECON_SCORER_URL; set that if this is meant to be the internal instance."
    )
ADMIN_TOKEN = _admin_token_env or _DEV_ADMIN_TOKEN

# Elo K-factor for online updates.
ELO_K = float(os.environ.get("BIO3D_ELO_K", "32"))

# Number of bootstrap resamples for Bradley-Terry confidence intervals.
BT_BOOTSTRAP = int(os.environ.get("BIO3D_BT_BOOTSTRAP", "200"))

# Evidence-scaled neutral-center prior for the VLM-JUDGE Bradley-Terry fit only
# (the human pairwise board keeps the unpenalized MLE). The judge's K-wise ballots are
# same-paradigm quads, so the comparison graph is disconnected by construction and full of
# all-win/all-loss records — an unpenalized MLE drives Elo to ±thousands. A prior that scales
# with each player's game count (a_p = max(FLOOR, FRAC*games_p) virtual wins+losses vs the
# strength-1 center) bounds every player toward the center regardless of volume/connectivity.
JUDGE_PRIOR_FRAC = float(os.environ.get("BIO3D_JUDGE_PRIOR_FRAC", "0.25"))
JUDGE_PRIOR_FLOOR = float(os.environ.get("BIO3D_JUDGE_PRIOR_FLOOR", "0.5"))

# --- Public-arena integrity / anti-abuse ---
# Rate limiting: at most VOTE_RATE_LIMIT votes per VOTE_RATE_WINDOW seconds per session.
VOTE_RATE_LIMIT = int(os.environ.get("BIO3D_VOTE_RATE_LIMIT", "60"))
VOTE_RATE_WINDOW = float(os.environ.get("BIO3D_VOTE_RATE_WINDOW", "60"))
# Second rate-limit layer keyed by client IP (same window). Caps throughput even when a farmer
# clears their session cookie to reset the per-session limit. Deliberately more generous than the
# per-session cap so NAT'd users (office/uni sharing one IP) aren't throttled as a group.
IP_VOTE_RATE_LIMIT = int(os.environ.get("BIO3D_IP_VOTE_RATE_LIMIT", "300"))
# Only trust X-Forwarded-For behind a known proxy (HF Spaces, Cloudflare) — otherwise a client
# can spoof it to dodge the per-IP limit. Off by default → use the socket peer address.
TRUST_FORWARDED_FOR = os.environ.get("BIO3D_TRUST_FORWARDED_FOR", "false").lower() in (
    "1",
    "true",
    "yes",
)
# Probability of serving a gold-standard attention-check pair instead of a real one.
GOLD_RATE = float(os.environ.get("BIO3D_GOLD_RATE", "0.1"))
# Sessions below this trust score are excluded from the authoritative BT leaderboard.
TRUST_THRESHOLD = float(os.environ.get("BIO3D_TRUST_THRESHOLD", "0.5"))
# Optional human-verification (captcha). Off by default so local/dev needs no keys.
#
# TWO keys are needed, and they are not interchangeable. The SECRET verifies a token
# server-side against the provider; the SITE key is public and is what the browser needs to
# render a widget at all. Only the secret existed until 2026-07-27, so there was no way to
# obtain a token — turning REQUIRE_CAPTCHA on would have rejected every vote with a 403.
REQUIRE_CAPTCHA = os.environ.get("BIO3D_REQUIRE_CAPTCHA", "false").lower() in ("1", "true", "yes")
CAPTCHA_PROVIDER = os.environ.get(
    "BIO3D_CAPTCHA_PROVIDER", "turnstile"
).lower()  # turnstile|hcaptcha
CAPTCHA_SITE_KEY = os.environ.get("BIO3D_CAPTCHA_SITE_KEY", "").strip()
CAPTCHA_SECRET = os.environ.get("BIO3D_CAPTCHA_SECRET", "").strip()
if REQUIRE_CAPTCHA and not (CAPTCHA_SITE_KEY and CAPTCHA_SECRET):
    # Fail loud, like the admin-token guard above. Enabled-but-unconfigured does not merely
    # weaken a control here — it makes voting impossible, and only on the deploy that has the
    # switch on. A boot failure is cheap; a silently unvotable arena is not.
    raise RuntimeError(
        "BIO3D_REQUIRE_CAPTCHA is on but the captcha is not configured. Set BOTH "
        "BIO3D_CAPTCHA_SITE_KEY (public, renders the widget) and BIO3D_CAPTCHA_SECRET "
        "(private, verifies the token), or leave BIO3D_REQUIRE_CAPTCHA unset."
    )

# --- Bad-output handling ---
# Vote pool drops outputs D-Complete classified into these completeness categories
# (clearly not a whole plant). Empty set disables the completeness auto-gate.
POOL_EXCLUDED_COMPLETENESS_CATEGORIES = {
    c.strip()
    for c in os.environ.get(
        "BIO3D_POOL_EXCLUDED_COMPLETENESS_CATEGORIES", "isolated-organ,fragment"
    ).split(",")
    if c.strip()
}
# Distinct-session flags before an output auto-hides. Flagging is a CURATOR-ONLY tool served
# on the internal instance (public deploy has no flag button and /api/flag 404s), so a single
# curator flag hides immediately — default 1, not a crowd-consensus threshold. Env-overridable.
FLAG_HIDE_THRESHOLD = int(os.environ.get("BIO3D_FLAG_HIDE_THRESHOLD", "1"))


# --- Semantic-admissibility predicate (VLM cardinality+identity) ---
# off: dormant (not in the rubric, no advisory flags). advisory: surfaces confident rejects to
# the ⚑ review queue as non-hiding flags but does NOT auto-exclude. gate: auto-excludes rejects
# from the vote pool. Default is `gate`: the semantic-v2 acceptance run cleared the zero-FP-on-good
# bar (0/232 real FPs on `complete` outputs, recall 13/32); see
# docs/results/2026-07-03-semantic-admissibility-results.md. Takes effect once scores are backfilled.
def _valid_semantic_mode(mode: str) -> str:
    """Fail loud on an unrecognized mode rather than silently disabling the predicate."""
    if mode not in ("off", "advisory", "gate"):
        raise ValueError(
            f"BIO3D_SEMANTIC_ADMISSIBILITY_MODE must be one of off|advisory|gate, got {mode!r}"
        )
    return mode


SEMANTIC_ADMISSIBILITY_MODE = _valid_semantic_mode(
    os.environ.get("BIO3D_SEMANTIC_ADMISSIBILITY_MODE", "gate").lower()
)

# --- Verified login (Hugging Face OAuth). Off unless client id+secret are set. ---
HF_CLIENT_ID = os.environ.get("BIO3D_HF_CLIENT_ID", "")
HF_CLIENT_SECRET = os.environ.get("BIO3D_HF_CLIENT_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("BIO3D_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Social / Open Graph share cards — a pasted link previews with a title, description and image.
SITE_NAME = "Bio 3D Arena"
SITE_TAGLINE = (
    "A blind benchmark arena for 3D generative models of real organisms — vote on which "
    "reconstruction best matches the real thing."
)
OG_IMAGE_PATH = os.environ.get("BIO3D_OG_IMAGE", "/static/og-default.png")
# Set the Secure flag on session cookies.
#
# This used to be DERIVED from PUBLIC_BASE_URL.startswith("https://") alone, which made cookie
# security a side effect of remembering an unrelated URL setting: a public deploy that forgot
# BIO3D_PUBLIC_BASE_URL silently shipped cookies without Secure (2026-07-26 audit, P1). The
# deploy type — not a URL string — is what decides this, so a public deploy defaults to Secure
# whatever the base URL says. An explicit BIO3D_COOKIE_SECURE still wins in BOTH directions
# (the old expression could only turn it on), for a public instance genuinely served over http.
_cookie_secure_env = os.environ.get("BIO3D_COOKIE_SECURE", "").strip().lower()
if _cookie_secure_env:
    COOKIE_SECURE = _cookie_secure_env in ("1", "true", "yes")
else:
    COOKIE_SECURE = IS_PUBLIC_DEPLOY or PUBLIC_BASE_URL.startswith("https://")

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
# RECON_SCORER_URL itself is read at the top of this file — the deploy-safety guards there
# need it — so this stays a single definition rather than a second env read that could drift.

# Public instances run with an empty scorer URL → scoring disabled (scores are promoted,
# never recomputed). Keeps the public deploy free of the Agrigen scoring microservice.
SCORING_ENABLED = not IS_PUBLIC_DEPLOY

# Internal research/analytics pages: /benchmark, /significance, /difficulty, /fidelity,
# /procedural, /trait/{id}. Served only on the internal instance; the public deploy hard-404s
# them and strips them from nav + cross-links, so novel methodology never reaches the public
# surface (same Agrigen-decoupling rationale as SCORING_ENABLED). Defaults to SCORING_ENABLED —
# the existing public-vs-internal signal — so no extra config is needed on either deploy;
# override with BIO3D_INTERNAL_PAGES=true/false.
_internal_pages_env = os.environ.get("BIO3D_INTERNAL_PAGES", "").strip().lower()
INTERNAL_PAGES_ENABLED = (
    _internal_pages_env in ("1", "true", "yes") if _internal_pages_env else SCORING_ENABLED
)

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

# Gallery slugs exempt from the "exclude recon input from the vote UI" rule (reference_images_for_task).
# barley-MRI is a root-system stand-in with no meaningful whole-plant CC gallery, so it keeps
# showing its input as the reference rather than being left with no anchor at all.
INPUT_REFERENCE_EXEMPT_SLUGS = {"hordeum_vulgare"}

# Generators hidden everywhere in the app UI by slug (kept in the DB for internal analysis) —
# dropped from the perceptual boards (mode_a_excluded_generator_ids) and the arena vote pool on
# every instance. Stricter than the public-export gate (which only drops them from the bundle).
#   agrigen/demeter/helios — AgriGen internal procedural-expert testers (also covered by the
#     procedural_expert paradigm hide, kept here belt-and-suspenders).
#   trellis/hunyuan3d — the bio3d-arena SELF-HOSTED early recon runs. They duplicate the
#     API-served TRELLIS/Hunyuan3D (fal/Replicate), aren't API-reproducible, and were the
#     low-quality early runs — pruned. (Self-hosted InstantMesh stays: it's the only InstantMesh.)
APP_HIDDEN_GENERATOR_SLUGS = frozenset({"agrigen", "demeter", "helios", "trellis", "hunyuan3d"})

# Same internal-data-only posture as APP_HIDDEN_GENERATOR_SLUGS, but keyed by output SOURCE
# rather than generator slug. xfrog uses one variant generator slug per crop (all named
# "XfrogPlants (botanical)"), so a slug list can't catch it cleanly; partcrafter is a single
# generator but a frontier: commercial model that would otherwise survive the display export.
# Both are kept in the DB for internal analysis but hidden from the whole app UI and never
# promoted to a public bundle (public_export.HARD_EXCLUDE_SOURCES carries the export gate).
APP_HIDDEN_SOURCES = frozenset({"found:xfrog", "frontier:partcrafter"})

# Same internal-data-only posture, but keyed by generator PARADIGM. These paradigms are kept in
# the DB for internal analysis but excluded from the whole app UI (arena pool, leaderboard,
# models, spotlight):
#   retrieval — found human-made assets (Sketchfab/Objaverse). Not a generative model; ranking
#     them here muddies "which model rebuilds life best" (that's a separate GT-creation benchmark).
#   procedural_expert — hand-authored rule-based generators (Blender/Infinigen/L-Py/AgriGen…),
#     which we can't meaningfully scale beyond the current handful. (procedural_llm, the
#     LLM-authored path, STAYS — it scales and is a core differentiator.)
#   capture_scan — photogrammetry / real-world capture; a data-capture reference, thin, kept for
#     internal analysis.
APP_HIDDEN_PARADIGMS = frozenset({"retrieval", "procedural_expert", "capture_scan"})


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
