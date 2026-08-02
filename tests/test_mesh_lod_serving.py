"""An LOD is only worth anything if it is actually the thing a ballot fetches first.

Stage 1 (`app/mesh_lod.py`) generates the file and proves it is safe. That is invisible to a voter
until three things line up: the export writes it into the bundle AND records that it did, the app
serves it under a URL of its own, and the payload advertises it. Each one fails silently on its
own — the ballot keeps working at exactly today's speed — so each is asserted here.

Route ordering is pinned here too. `/media/o/{output_id}.{ext}` is registered second on purpose,
since its `{output_id}` would otherwise capture `580.lod` out of `580.lod.glb`.

MEASURED rather than assumed: with the two routes reordered at runtime, that request returns
**422**, not the full mesh — `output_id: int` rejects `"580.lod"` before any handler runs. The
failure is loud, so this ordering is less dangerous than it looks. The guard is kept because the
dependency between the two routes is invisible at either call site, not because it prevents a
silent success. (An earlier draft of this docstring asserted exactly that silent-200 story;
running the reordered case disproved it.)
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.database import SessionLocal
from app.main import _arena_lod_url, app
from app.models import ModelOutput
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


@pytest.fixture()
def glb_output():
    with SessionLocal() as db:
        o = (
            db.query(ModelOutput)
            .filter(ModelOutput.asset_path.isnot(None), ModelOutput.asset_format == "glb")
            .first()
        )
        assert o is not None, "seed produced no GLB output"
        yield o


# --------------------------------------------------------------------------- payload advertising


def test_no_lod_flag_means_no_lod_url(glb_output):
    """The safe direction. An output whose meta says nothing keeps today's behaviour exactly:
    the ballot fetches the full mesh, as it always has."""
    glb_output.meta_json = json.dumps({"sha256": "x"})
    assert _arena_lod_url(glb_output) is None


def test_lod_flag_advertises_a_distinct_url(glb_output):
    glb_output.meta_json = json.dumps({"lod": True})
    url = _arena_lod_url(glb_output)
    assert url == f"/media/o/{glb_output.id}.lod.glb"
    assert url != f"/media/o/{glb_output.id}.glb", "LOD must not share the full mesh's URL"


def test_unparseable_meta_does_not_advertise_an_lod(glb_output):
    """Garbage in meta_json must degrade to the full mesh, never to a URL that 404s in front of
    a voter mid-ballot."""
    glb_output.meta_json = "{not json"
    assert _arena_lod_url(glb_output) is None


def test_non_glb_never_advertises_an_lod(glb_output):
    """Point clouds and volumes are mounted by a different viewer entirely; a .lod.glb URL for
    one would be meaningless."""
    glb_output.meta_json = json.dumps({"lod": True})
    glb_output.asset_format = "ply"
    assert _arena_lod_url(glb_output) is None


# --------------------------------------------------------------------------- routing


def test_lod_url_reaches_the_lod_route(glb_output):
    """The seeded output has no LOD on disk, so a correctly-routed request 404s.

    Verified against the broken ordering by moving the LOD route after the generic one at runtime:
    that returns 422 (`output_id: int` cannot parse "1.lod"). So this assertion does fail when the
    ordering regresses — just with a different code than the docstring originally claimed.
    """
    r = client.get(f"/media/o/{glb_output.id}.lod.glb")
    assert r.status_code == 404, (
        f"expected 404 from the LOD route; got {r.status_code}. A 422 means the generic "
        "/media/o/{output_id}.{ext} route is registered first and is swallowing 'lod.glb'."
    )


def test_full_mesh_url_still_works(glb_output):
    """Positive control: the negative above must not pass because media serving is broken."""
    r = client.get(f"/media/o/{glb_output.id}.glb")
    assert r.status_code == 200


def test_lod_is_served_when_the_file_exists(glb_output):
    """The whole point, end to end: a real .lod.glb on disk is what comes back."""
    from app import mesh_lod

    rel_lod = mesh_lod.lod_path(glb_output.asset_path)
    target = config.ASSET_DIR / rel_lod
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = b"glTF-pretend-lod-bytes"
    target.write_bytes(payload)
    try:
        r = client.get(f"/media/o/{glb_output.id}.lod.glb")
        assert r.status_code == 200
        assert r.content == payload, "served something other than the LOD file"
        assert r.headers.get("etag"), "LOD must be revalidatable like every other mesh"
        assert "cache-control" in r.headers
    finally:
        target.unlink(missing_ok=True)


def test_unknown_output_is_404_not_500():
    r = client.get("/media/o/99999999.lod.glb")
    assert r.status_code == 404


# --------------------------------------------------------------------------- export stamping


def test_stamp_preserves_existing_meta():
    """meta_json already carries sha256/vertices/ingested. Stamping the LOD flag must not be a
    silent overwrite of provenance the rest of the system reads."""
    from scripts.export_public import _stamp_lod

    row = {"meta_json": json.dumps({"sha256": "abc", "vertices": 105628})}
    _stamp_lod(row)
    meta = json.loads(row["meta_json"])
    assert meta["lod"] is True
    assert meta["sha256"] == "abc"
    assert meta["vertices"] == 105628


def test_stamp_survives_absent_or_broken_meta():
    from scripts.export_public import _stamp_lod

    for bad in ({}, {"meta_json": None}, {"meta_json": ""}, {"meta_json": "{not json"}):
        row = dict(bad)
        _stamp_lod(row)
        assert json.loads(row["meta_json"])["lod"] is True


def test_rows_are_serialised_after_assets_are_staged():
    """Ordering guard, and it is not theoretical: this pipeline wrote rows.json BEFORE staging
    assets until the LOD pass existed. Staging is what stamps `meta_json.lod`, so under the old
    order every flag was written to the bundle unstamped and no LOD was ever advertised — while
    the files themselves shipped and every count in the manifest looked right.

    Asserted on source order rather than by running a full export, which needs a Node toolchain
    this repo does not require for tests.
    """
    import inspect

    from scripts import export_public

    src = inspect.getsource(export_public.export_bundle)
    stage_at = src.index("_stage_assets(")
    rows_at = src.index('(out / "rows.json").write_bytes')
    assert stage_at < rows_at, (
        "rows.json is serialised before _stage_assets stamps meta_json.lod — the flag cannot "
        "reach the bundle and no voter will ever be served an LOD"
    )
