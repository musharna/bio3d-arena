"""The bundle uploader must REPLACE a changed blob, not skip it because the key exists.

Found while running a real release. The uploader's skip predicate was `storage.exists(rel)`,
which is right for its original purpose — resuming an interrupted upload of new content, where
re-running should cost a HEAD rather than a PUT — and wrong for the case that actually dominates
a release: **every key already exists**, holding the previous version of that mesh.

With that predicate the import would have skipped all 527 assets, reported success, and changed
nothing. A 9.1x recompression of the entire corpus (4.05 GB -> 0.44 GB) would have reached no
one, and the failure is invisible: the counts read "already_present: 527", which looks exactly
like a correct resume.

The fix is to compare identity, not existence. These tests pin both halves — a changed blob is
uploaded, an identical one is still skipped — because a fix that simply always uploads would
throw away the resumability that exists for a good reason (a real release died 40 minutes in on
one corrupted TLS record).
"""

from __future__ import annotations

import json

import pytest

from app.storage import LocalStorageBackend
from scripts import import_public


@pytest.fixture
def bundle(tmp_path):
    b = tmp_path / "bundle"
    (b / "assets" / "uploads").mkdir(parents=True)
    (b / "gt").mkdir(parents=True)
    (b / "assets" / "uploads" / "a.glb").write_bytes(b"COMPRESSED-small")
    (b / "assets" / "uploads" / "b.glb").write_bytes(b"COMPRESSED-also-small")
    (b / "manifest.json").write_text(json.dumps({"n_outputs": 2}))
    return b


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "remote"
    root.mkdir()
    return LocalStorageBackend(root)


def test_a_changed_blob_is_replaced(bundle, store):
    """The release case: the key exists, holding the OLD, larger mesh."""
    store.save("uploads/a.glb", b"x" * 5000)  # the uncompressed predecessor
    store.save("uploads/b.glb", b"x" * 5000)

    res = import_public._upload_assets(bundle, store)

    assert res["uploaded"] == 2, "changed blobs were skipped — the release would be a no-op"
    assert res["replaced"] == 2
    assert store.read("uploads/a.glb") == b"COMPRESSED-small"
    assert store.read("uploads/b.glb") == b"COMPRESSED-also-small"


def test_an_identical_blob_is_still_skipped(bundle, store):
    """Positive control: resumability must survive the fix. Without this, an
    always-upload implementation would pass the test above and quietly re-send gigabytes
    on every retry."""
    store.save("uploads/a.glb", b"COMPRESSED-small")
    store.save("uploads/b.glb", b"COMPRESSED-also-small")

    res = import_public._upload_assets(bundle, store)

    assert res["uploaded"] == 0
    assert res["already_present"] == 2
    assert res["replaced"] == 0


def test_a_missing_blob_is_uploaded(bundle, store):
    res = import_public._upload_assets(bundle, store)
    assert res["uploaded"] == 2
    assert res["replaced"] == 0, "nothing was there to replace"


def test_size_reports_none_for_an_absent_object(store):
    """`size()` is the predicate's whole basis; None must mean absent and never 0, or an
    empty remote object would read as 'missing' forever."""
    assert store.size("nope.glb") is None
    store.save("empty.glb", b"")
    assert store.size("empty.glb") == 0
