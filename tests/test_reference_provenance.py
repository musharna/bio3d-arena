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


@pytest.mark.parametrize("slug", ["arabidopsis", "pinus"])
def test_reference_has_image_and_valid_provenance(slug):
    img = f"data/assets/reference/{slug}_ref.jpg"
    meta = f"data/assets/reference/{slug}_ref.json"
    assert os.path.exists(img) and os.path.getsize(img) > 5000, img
    assert os.path.exists(meta), meta
    d = json.load(open(meta))
    assert REQUIRED <= set(d), REQUIRED - set(d)
    assert d["file"] == f"{slug}_ref.jpg"
    assert d["license"] in ALLOWED_LICENSES, d["license"]
    assert d["source_url"].startswith("http") and d["download_url"].startswith("http")
