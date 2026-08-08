# tests/test_reference_independence.py
"""A reference photo must not be the photo a reconstruction was generated FROM.

`reference_images_for_task` already refuses to show a task's `meta_json.input_image`, but that
suppresses one ASSET PATH. The gallery is sourced separately from iNaturalist, so the same
underlying photo can arrive as a different file at a different rendition and be shown anyway —
measured 2026-08-08 on zea_mays, morchella_esculenta and trametes_versicolor.
"""

import json

import pytest

from app import reference_qa

INAT_INPUT = "https://inaturalist-open-data.s3.amazonaws.com/photos/27005174/original.jpg"
INAT_GALLERY = "https://inaturalist-open-data.s3.amazonaws.com/photos/27005174/medium.jpg"
INAT_OTHER = "https://inaturalist-open-data.s3.amazonaws.com/photos/99999999/medium.jpg"


def _ids(*urls: str) -> set[str]:
    """Identity set for the given urls, asserting each one actually parsed."""
    out = set()
    for u in urls:
        ident = reference_qa.photo_identity(u)
        assert ident is not None, f"expected {u!r} to yield an identity"
        out.add(ident)
    return out


def test_photo_identity_is_stable_across_renditions():
    """original.jpg and medium.jpg of one photo are the SAME photo — the bug byte-hashing missed."""
    assert reference_qa.photo_identity(INAT_INPUT) == reference_qa.photo_identity(INAT_GALLERY)
    assert reference_qa.photo_identity(INAT_INPUT) != reference_qa.photo_identity(INAT_OTHER)
    assert reference_qa.photo_identity(None) is None
    assert reference_qa.photo_identity("https://example.com/nothing.jpg") is None


def test_wikimedia_identity_matches_across_thumb_sizes():
    base = "https://upload.wikimedia.org/wikipedia/commons"
    full = f"{base}/6/6f/DoubleDelightHybridTeaRoseBush-4.jpg"
    thumb = f"{base}/thumb/6/6f/DoubleDelightHybridTeaRoseBush-4.jpg/1280px-Double.jpg"
    assert reference_qa.photo_identity(full) == reference_qa.photo_identity(thumb)


def test_recon_input_photo_ids_reads_sidecars(tmp_path):
    (tmp_path / "turkeytail_ref.json").write_text(
        json.dumps({"file": "turkeytail_ref.jpg", "download_url": INAT_INPUT})
    )
    (tmp_path / "notes.txt").write_text("ignored")
    ids = reference_qa.recon_input_photo_ids(tmp_path)
    assert ids == {reference_qa.photo_identity(INAT_INPUT)}


def test_gallery_photo_that_is_the_recon_input_is_not_independent():
    ids = _ids(INAT_INPUT)

    leaked = reference_qa.assess_independence(
        photo_id=27005174, url=INAT_GALLERY, input_photo_ids=ids
    )
    assert leaked["ok"] is False

    # Positive control IN THE SAME TEST: an unrelated photo must still pass, or a broken
    # harness that fails everything would read as a working gate.
    clean = reference_qa.assess_independence(photo_id=99999999, url=INAT_OTHER, input_photo_ids=ids)
    assert clean["ok"] is True


def test_photo_id_alone_is_enough_when_url_is_missing():
    """Manifests carry photo_id directly; a missing url must not silently pass the check."""
    ids = _ids(INAT_INPUT)
    assert (
        reference_qa.assess_independence(photo_id=27005174, url=None, input_photo_ids=ids)["ok"]
        is False
    )


def test_qa_verdict_fails_and_says_why():
    ids = _ids(INAT_INPUT)
    ind = reference_qa.assess_independence(photo_id=27005174, url=INAT_GALLERY, input_photo_ids=ids)

    verdict = reference_qa.qa_reference_image(independence=ind)
    assert verdict["passed"] is False
    assert any("input" in r.lower() for r in verdict["reasons"])

    # positive control: the same combiner still passes a clean image
    ok = reference_qa.assess_independence(photo_id=99999999, url=INAT_OTHER, input_photo_ids=ids)
    assert reference_qa.qa_reference_image(independence=ok)["passed"] is True


def test_no_input_set_means_no_opinion():
    """With no known inputs the predicate must abstain, not fail everything closed."""
    assert (
        reference_qa.assess_independence(
            photo_id=27005174, url=INAT_GALLERY, input_photo_ids=set()
        )["ok"]
        is True
    )


@pytest.mark.parametrize("bad", [{}, {"file": "x.jpg"}])
def test_sidecar_without_a_url_is_skipped_not_crashed(tmp_path, bad):
    (tmp_path / "x_ref.json").write_text(json.dumps(bad))
    assert reference_qa.recon_input_photo_ids(tmp_path) == set()
