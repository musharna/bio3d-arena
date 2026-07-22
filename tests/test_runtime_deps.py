"""The public web instance must not need the research/scoring stack.

`requirements.txt` is what a deployer installs (deploy/README.md step 4). Everything the serving
app can reach at request time belongs there; everything else belongs in requirements-research.txt
or requirements-dev.txt. The expensive member of that second group is open_clip_torch, which drags
torch plus the whole NVIDIA CUDA stack (multiple GB) onto a web host that never imports it.

Nothing structural stops a future edit from adding `import torch` at the top of an app module and
silently re-coupling the two. This test is that stop: it boots the real ASGI app in a SUBPROCESS
(sys.modules is process-global, so an in-process check would be corrupted by whichever test
imported tifffile first), serves the public routes, and asserts the heavy modules stayed unloaded.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Offline-only: the request path never reaches these. Kept out of requirements.txt.
#   torch/open_clip -> app.species_id, reached only via reference_qa <- input_verify <- a script
#   scipy/skimage/nibabel/tifffile -> app.volume_convert, imported only by scripts + tests
#   fast_simplification -> trimesh's decimation backend for app.mesh_convert (scripts + tests)
#   anthropic -> the VLM judge; not imported anywhere under app/
FORBIDDEN_AT_RUNTIME = [
    "torch",
    "open_clip",
    "scipy",
    "skimage",
    "nibabel",
    "tifffile",
    "fast_simplification",
    "anthropic",
]

# Public-instance posture: scoring off. These are the routes deploy/README.md smoke-tests.
_PROBE = """
import json, os, sys, tempfile
tmp = tempfile.mkdtemp(prefix="bio3d_test_runtimedeps_")
os.environ["BIO3D_DATA_DIR"] = tmp
os.environ["BIO3D_DB_PATH"] = os.path.join(tmp, "bio3d_test_runtime.db")
os.environ.pop("BIO3D_DATABASE_URL", None)
os.environ["BIO3D_RECON_SCORER_URL"] = ""
sys.path.insert(0, %(root)r)

from app.database import Base, engine
import app.models  # noqa: F401

Base.metadata.create_all(bind=engine)

from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as c:
    for route in ["/", "/leaderboard", "/coverage", "/terms", "/licenses", "/arena"]:
        c.get(route)

print("BIO3D_LOADED=" + json.dumps(sorted(m for m in %(forbidden)r if m in sys.modules)))
"""


def test_serving_the_public_app_never_imports_the_research_stack():
    probe = _PROBE % {"root": str(REPO_ROOT), "forbidden": FORBIDDEN_AT_RUNTIME}
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("BIO3D_LOADED=")]
    assert line, f"probe produced no verdict:\n{proc.stdout}\n{proc.stderr}"
    loaded = json.loads(line[-1].split("=", 1)[1])
    assert loaded == [], (
        f"serving the public app loaded research-only modules: {loaded}. Either import them "
        "lazily (inside the function that needs them, as app.species_id does), or move the "
        "dependency into requirements.txt and update deploy/README.md — but note that puts the "
        "full CUDA stack on the public web host."
    )


def test_requirements_files_partition_the_dependencies():
    """Runtime / research / dev are layered, and the heavy stack is NOT in the runtime file."""
    runtime = (REPO_ROOT / "requirements.txt").read_text()
    research = (REPO_ROOT / "requirements-research.txt").read_text()
    dev = (REPO_ROOT / "requirements-dev.txt").read_text()

    for pkg in ("open_clip_torch", "anthropic", "scipy", "scikit-image", "nibabel", "tifffile"):
        assert pkg not in runtime, f"{pkg} is research-only; it must not be in requirements.txt"
    for pkg in ("pytest", "ruff"):
        assert pkg not in runtime, f"{pkg} is a dev tool; it must not be in requirements.txt"

    # The web app genuinely needs these — /api/submit reaches ingest -> trimesh/structural -> numpy.
    for pkg in ("fastapi", "SQLAlchemy", "trimesh", "numpy", "pillow"):
        assert pkg in runtime, f"{pkg} is request-reachable and must stay in requirements.txt"

    # Layered, so a research or dev install is a superset of runtime rather than a rival list.
    assert "-r requirements.txt" in research
    assert "-r requirements-research.txt" in dev
