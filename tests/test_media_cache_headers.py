"""Arena meshes must be cacheable, and must revalidate rather than be trusted forever.

Measured against the live instance 2026-07-31: `GET /media/o/553.glb` returned **no
Cache-Control, no ETag, no Last-Modified**. Nothing about an arena mesh was cacheable, so:

* the same model reappearing in a later ballot was re-downloaded in full;
* a reload or back-navigation re-downloaded every mesh on screen;
* and any prefetch is not merely useless but a REGRESSION -- it would spend the bytes, cache
  nothing, and spend them again on render.

The production path is the one that was broken. `media_asset` branches: local storage returns a
`FileResponse` (Starlette attaches etag + last-modified itself), while REMOTE storage -- which is
what the public deploy runs -- returned a bare `Response` with no headers at all. A test that
only exercised the local branch would pass while production stayed uncached, so the remote branch
is asserted explicitly here.

Why a bounded max-age instead of `immutable`: these URLs are NOT content-addressed, and blobs DO
get replaced in place. The 2026-07-31 release rewrote 581 objects at their existing keys during
the Draco/texture recompression. `immutable, max-age=1y` would have pinned voters to the old
uncompressed meshes for a year. A short max-age plus a strong validator gives the within-session
reuse that matters while keeping a release's replacement visible almost immediately.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.database import SessionLocal
from app.main import app
from app.models import ModelOutput
from app.seed import seed_all

client = TestClient(app)

#: Long enough that meshes are reused across a voting session, short enough that a release which
#: replaces a blob in place reaches voters quickly.
MAX_MAX_AGE = 24 * 3600


def setup_module(_module):
    seed_all(force=True)


@pytest.fixture(scope="module")
def output_id() -> int:
    with SessionLocal() as db:
        o = db.query(ModelOutput).filter(ModelOutput.asset_path.isnot(None)).first()
        assert o is not None, "seed produced no output with an asset"
        return o.id


def _max_age(resp) -> int:
    cc = resp.headers.get("cache-control", "")
    m = re.search(r"max-age=(\d+)", cc)
    assert m, f"no max-age in Cache-Control: {cc!r}"
    return int(m.group(1))


def test_local_storage_asset_is_cacheable(output_id):
    r = client.get(f"/media/o/{output_id}.glb")
    assert r.status_code == 200
    assert "cache-control" in r.headers, "no Cache-Control — every ballot re-downloads"
    assert r.headers.get("etag"), "no validator, so a stale copy can never be revalidated cheaply"


def test_remote_storage_asset_is_cacheable(monkeypatch, output_id):
    """THE PRODUCTION PATH. This is the branch that shipped with zero cache headers."""
    with SessionLocal() as db:
        o = db.get(ModelOutput, output_id)
        payload = b"glTF" + b"\x00" * 2048
        path = o.asset_path

    class FakeRemote:
        remote = True

        def read(self, rel):
            assert rel == path
            return payload

    monkeypatch.setattr(app_main, "storage", FakeRemote())
    r = client.get(f"/media/o/{output_id}.glb")
    assert r.status_code == 200
    assert r.content == payload
    assert "cache-control" in r.headers, "remote (production) branch served no Cache-Control"
    assert r.headers.get("etag"), "remote branch served no validator"


def test_cache_lifetime_is_bounded(output_id):
    """A release replaces blobs at the SAME url (581 of them on 2026-07-31), so an unbounded or
    `immutable` lifetime would serve stale geometry long after it was replaced."""
    r = client.get(f"/media/o/{output_id}.glb")
    assert _max_age(r) <= MAX_MAX_AGE, "cache lifetime outlives an in-place blob replacement"
    assert "immutable" not in r.headers.get("cache-control", ""), (
        "these URLs are not content-addressed; immutable would pin voters to replaced meshes"
    )


def test_a_matching_validator_skips_the_body(output_id):
    """The point of the validator: a revalidation costs a round trip, not another 8 MB."""
    first = client.get(f"/media/o/{output_id}.glb")
    etag = first.headers["etag"]
    again = client.get(f"/media/o/{output_id}.glb", headers={"If-None-Match": etag})
    assert again.status_code == 304, "matching ETag must return 304, not the whole mesh again"
    assert not again.content


def test_a_changed_blob_gets_a_new_validator(monkeypatch, output_id):
    """The validator must track CONTENT. If it were derived from the id alone it would keep
    matching after a re-export replaced the bytes, and voters would hold the old mesh until the
    max-age expired."""
    with SessionLocal() as db:
        path = db.get(ModelOutput, output_id).asset_path

    class FakeRemote:
        remote = True

        def __init__(self, body):
            self.body = body

        def read(self, rel):
            assert rel == path
            return self.body

    monkeypatch.setattr(app_main, "storage", FakeRemote(b"glTF" + b"\x01" * 512))
    a = client.get(f"/media/o/{output_id}.glb").headers["etag"]
    monkeypatch.setattr(app_main, "storage", FakeRemote(b"glTF" + b"\x02" * 4096))
    b = client.get(f"/media/o/{output_id}.glb").headers["etag"]
    assert a != b, "validator did not change when the underlying blob did"
