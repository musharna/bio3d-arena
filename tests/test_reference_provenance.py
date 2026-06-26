import json
import os
import pytest

REQUIRED = {
    "subject",
    "file",
    "source",
    "source_url",
    "download_url",
    "license",
    "author",
    "attribution",
    "title",
    "note",
}
ALLOWED_LICENSES = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0"}
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("slug", ["arabidopsis", "pinus"])
def test_reference_has_image_and_valid_provenance(slug):
    img = os.path.join(REPO_ROOT, f"data/assets/reference/{slug}_ref.jpg")
    meta = os.path.join(REPO_ROOT, f"data/assets/reference/{slug}_ref.json")
    if not (os.path.exists(img) and os.path.exists(meta)):
        # data/ is gitignored runtime state — absent on a checkout without the runtime volume.
        pytest.skip(f"runtime reference asset absent (gitignored): {slug}")
    assert os.path.getsize(img) > 5000, img
    with open(meta) as f:
        d = json.load(f)
    assert REQUIRED <= set(d), REQUIRED - set(d)
    assert d["file"] == f"{slug}_ref.jpg"
    assert d["license"] in ALLOWED_LICENSES, d["license"]
    assert d["source_url"].startswith("http") and d["download_url"].startswith("http")
    for k in ("author", "attribution", "title", "subject"):
        assert d.get(k, "").strip(), k
