"""The heavy 3D viewer libraries must load only on pages that actually mount a viewer.

model-viewer (and viewer.js) used to sit in the global base.html <head>, so every page —
leaderboard, models, methodology, all the data-dense pages people browse — paid to download
a 3D renderer it never used. This guards the opt-in split: viewer pages carry the script,
non-viewer pages do not, and a future page that forgets to opt in (or opts in by mistake)
trips this test.
"""

from fastapi.testclient import TestClient

from app.database import init_db
from app.main import app

# The model-viewer <script src>, unique enough to detect on a rendered page.
# SELF-HOSTED since 2026-08-08 — this used to be the ajax.googleapis.com URL. Blocking that host
# rendered 0/6 viewers with no error shown, so Google was a single point of failure for the only
# thing the site does.
MODEL_VIEWER = "vendor/model-viewer-3.5.0.min.js"
# Guard the reason for the change, not just its result: if anyone points the tag back at a CDN,
# the assertion below fails even though a renderer would still load.
GOOGLE_CDN = "ajax.googleapis.com"


def setup_module(_m):
    init_db()


client = TestClient(app)


def test_viewer_pages_load_model_viewer():
    """Pages that mount a 3D model must ship the renderer."""
    for path in ("/", "/arena"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert MODEL_VIEWER in r.text, f"{path} should load model-viewer but does not"
        assert GOOGLE_CDN not in r.text, (
            f"{path} loads model-viewer from a Google CDN again — self-hosting exists because "
            f"blocking that host left 0/6 viewers rendering, silently"
        )


def test_non_viewer_pages_omit_model_viewer():
    """Data pages that never mount a 3D model must NOT ship the renderer."""
    for path in ("/leaderboard", "/models", "/methodology"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert MODEL_VIEWER not in r.text, f"{path} should not load model-viewer but does"
