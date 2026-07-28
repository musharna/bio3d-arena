"""The CC reference galleries have to reach the public instance, and be READ the way they get there.

On the live deploy every task returned `references: []`. The galleries exist (17 taxa, 130
photos) but in neither place a public instance can see them:

  * `.dockerignore` excludes `data/`, so the image has no gallery — `/data/assets` on the running
    machine was empty, `/data/assets/reference` absent entirely.
  * the release bundle never carried them: `_bundle_assets` walks only `assets/` (output blobs)
    and `gt/`, and the export wrote neither, so nothing ever reached R2.

`reference_images_for_task` builds its image URLs through `storage.url_for()` but tested the
MANIFEST with `Path.exists()` against the local filesystem. That one local read is what made the
whole gallery vanish in production while working perfectly in dev — the classic
works-locally/silently-nothing-in-prod split, same shape as the two Postgres-only failures this
deploy already surfaced.

So: the export ships the galleries into the bundle under `assets/reference/gallery/**` (where
the existing uploader picks them up unchanged), and the app reads the manifest through the
storage backend like every other asset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config, service
from app.storage import LocalStorageBackend


def _write_gallery(root: Path, slug: str, items: list[dict]) -> None:
    d = root / "reference" / "gallery" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(items))
    for it in items:
        (d / it["file"]).write_bytes(b"\xff\xd8\xff jpeg-ish")


class _Task:
    """Only what reference_images_for_task touches."""

    def __init__(self, title, id=1):
        self.title = title
        self.id = id


@pytest.fixture
def gallery_in_storage(tmp_path, monkeypatch):
    """A storage root holding the gallery, with config.ASSET_DIR pointed SOMEWHERE ELSE.

    That split is the whole point: it reproduces the production topology, where assets live in
    object storage and the local asset dir is empty. A test that let both point at the same
    directory would pass against the original local-filesystem read and prove nothing.
    """
    store_root = tmp_path / "store"
    empty_local = tmp_path / "empty-local"
    store_root.mkdir()
    empty_local.mkdir()
    _write_gallery(
        store_root,
        "glycine_max",
        [
            {"file": "1.jpg", "license": "cc0", "attribution": "no rights reserved"},
            {"file": "2.jpg", "license": "cc-by", "attribution": "(c) Someone, CC BY"},
        ],
    )
    monkeypatch.setattr(config, "ASSET_DIR", empty_local)
    monkeypatch.setattr(
        service, "get_storage", lambda: LocalStorageBackend(store_root), raising=False
    )
    service.reference_gallery_cache_clear()
    yield store_root
    service.reference_gallery_cache_clear()


def test_gallery_is_read_through_storage_not_the_local_filesystem(gallery_in_storage):
    """THE regression. config.ASSET_DIR is empty; only storage has the gallery. Before the fix
    this returned [] — exactly what the live site did."""
    refs = service.reference_images_for_task(None, _Task("Glycine max — single-image → 3D"))
    assert len(refs) == 2, refs
    assert all(r["url"].endswith((".jpg",)) for r in refs)
    assert "reference/gallery/glycine_max/1.jpg" in refs[0]["url"]


def test_attribution_rides_along_as_credit(gallery_in_storage):
    """cc-by photos are only displayable WITH their attribution, so it must survive the trip."""
    refs = service.reference_images_for_task(None, _Task("Glycine max — x"))
    assert refs[1]["credit"] == "(c) Someone, CC BY"


def test_missing_gallery_is_an_empty_list_not_an_error(gallery_in_storage):
    """Not every task has a gallery; a miss is normal and must not 500 the arena."""
    assert service.reference_images_for_task(None, _Task("Nothing Here — x")) == []


def test_qa_failed_images_are_not_shown(tmp_path, monkeypatch):
    store = tmp_path / "s"
    store.mkdir()
    _write_gallery(
        store,
        "rosa",
        [
            {"file": "1.jpg", "attribution": "a"},
            {"file": "2.jpg", "attribution": "b", "passed_qa": False},
        ],
    )
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path / "nope")
    monkeypatch.setattr(service, "get_storage", lambda: LocalStorageBackend(store), raising=False)
    service.reference_gallery_cache_clear()
    refs = service.reference_images_for_task(None, _Task("Rosa — x"))
    assert len(refs) == 1 and "1.jpg" in refs[0]["url"]
    service.reference_gallery_cache_clear()


# --- the export half ----------------------------------------------------------------


def test_export_writes_the_gallery_where_the_uploader_will_find_it(tmp_path, monkeypatch):
    """The bundle key the uploader derives must be EXACTLY the key the app later asks storage
    for. Getting this wrong uploads real files under a path nothing reads — which looks like a
    successful publish and still shows no images."""
    from scripts.export_public import copy_reference_gallery
    from scripts.import_public import _bundle_assets

    src = tmp_path / "assets"
    _write_gallery(src, "zea_mays", [{"file": "1.jpg", "attribution": "a"}])
    monkeypatch.setattr(config, "ASSET_DIR", src)

    bundle = tmp_path / "bundle"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "gt").mkdir(parents=True)
    n = copy_reference_gallery(bundle, "display")
    assert n == 1

    keys = {k for k, _ in _bundle_assets(bundle)}
    assert "reference/gallery/zea_mays/1.jpg" in keys
    assert "reference/gallery/zea_mays/manifest.json" in keys


def test_export_prunes_qa_failed_entries_from_the_shipped_manifest(tmp_path, monkeypatch):
    """Shipping a manifest entry whose file was withheld would render a broken image."""
    from scripts.export_public import copy_reference_gallery

    src = tmp_path / "assets"
    _write_gallery(
        src,
        "rosa",
        [
            {"file": "1.jpg", "attribution": "a"},
            {"file": "2.jpg", "attribution": "b", "passed_qa": False},
        ],
    )
    monkeypatch.setattr(config, "ASSET_DIR", src)
    bundle = tmp_path / "b"
    (bundle / "assets").mkdir(parents=True)
    copy_reference_gallery(bundle, "display")

    shipped = json.loads((bundle / "assets/reference/gallery/rosa/manifest.json").read_text())
    assert [i["file"] for i in shipped] == ["1.jpg"]
    assert not (bundle / "assets/reference/gallery/rosa/2.jpg").exists()


def test_redistribute_posture_refuses_an_unredistributable_photo(tmp_path, monkeypatch):
    """Shipping the photo FILES is redistribution, so that posture gates on the license — the
    same rule the mesh export already applies. `display` does not redistribute (attributed
    display only), mirroring assert_recon_photos_cleared being redistribution-only."""
    from scripts.export_public import copy_reference_gallery

    src = tmp_path / "assets"
    _write_gallery(src, "rosa", [{"file": "1.jpg", "license": "cc-by-nc", "attribution": "a"}])
    monkeypatch.setattr(config, "ASSET_DIR", src)
    bundle = tmp_path / "b"
    (bundle / "assets").mkdir(parents=True)

    with pytest.raises(Exception, match="(?i)licen"):
        copy_reference_gallery(bundle, "redistribute")


def test_display_posture_ships_the_same_photo(tmp_path, monkeypatch):
    """Positive control for the gate: it must bite on redistribute ONLY. Without this, a gate
    that raised in both postures would satisfy the test above and silently ship nothing."""
    from scripts.export_public import copy_reference_gallery

    src = tmp_path / "assets"
    _write_gallery(src, "rosa", [{"file": "1.jpg", "license": "cc-by-nc", "attribution": "a"}])
    monkeypatch.setattr(config, "ASSET_DIR", src)
    bundle = tmp_path / "b"
    (bundle / "assets").mkdir(parents=True)
    assert copy_reference_gallery(bundle, "display") == 1


def test_absent_gallery_tree_is_not_an_export_failure(tmp_path, monkeypatch):
    from scripts.export_public import copy_reference_gallery

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path / "no-such-dir")
    bundle = tmp_path / "b"
    (bundle / "assets").mkdir(parents=True)
    assert copy_reference_gallery(bundle, "display") == 0
