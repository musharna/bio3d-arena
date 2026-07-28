"""Images stored in object storage must carry an image content type.

`_CONTENT_TYPES` only ever listed 3D formats, because images were only ever served by the LOCAL
backend, where StaticFiles derives the type from the filename and the stored metadata is
irrelevant. Publishing the reference galleries to R2 made images travel the S3 path for the
first time, and every .jpg landed as `application/octet-stream`.

Browsers sniff `<img>` bodies, so the photos do render today — which is exactly what makes this
worth pinning down: it is invisible until someone adds `X-Content-Type-Options: nosniff` or a
stricter CSP, at which point every reference photo silently stops loading and the cause is three
layers away from the symptom.
"""

from __future__ import annotations

import pytest

from app.storage import content_type_for


@pytest.mark.parametrize(
    "name,expected",
    [
        ("reference/gallery/zea_mays/1.jpg", "image/jpeg"),
        ("x.jpeg", "image/jpeg"),
        ("x.png", "image/png"),
        ("x.webp", "image/webp"),
        ("reference/gallery/zea_mays/manifest.json", "application/json"),
    ],
)
def test_web_asset_types_are_declared(name, expected):
    assert content_type_for(name) == expected


def test_uppercase_extensions_still_resolve():
    """Real corpora carry .JPG. Falling back to octet-stream on case alone would be silent."""
    assert content_type_for("PHOTO.JPG") == "image/jpeg"


def test_3d_formats_are_unchanged():
    """Positive control — the pre-existing entries must survive the addition."""
    assert content_type_for("a.glb") == "model/gltf-binary"
    assert content_type_for("a.pdb") == "chemical/x-pdb"


def test_unknown_extension_is_still_octet_stream():
    assert content_type_for("a.wat") == "application/octet-stream"
