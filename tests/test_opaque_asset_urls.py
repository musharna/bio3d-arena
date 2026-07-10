"""The anonymized arena payloads must never expose the real asset_path in the model URL — a
descriptive path like `gold/task19__bad.glb` reveals which side is the planted-bad gold decoy
(and generator-named files leak identity) to anyone who opens devtools. The URL is opaque and
output-scoped; a resolver route serves the bytes by output id."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app import config, main
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Category, Comparison, Criterion, Generator, ModelOutput, Task

client = TestClient(app)


def _seed_pair(asset_a: str, asset_b: str):
    init_db()
    with SessionLocal() as db:
        cat = Category(slug=f"c-{uuid.uuid4().hex[:8]}", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title=f"t-{uuid.uuid4().hex[:8]}", prompt="p")
        crit = Criterion(slug=f"cr-{uuid.uuid4().hex[:6]}", name="Overall")
        db.add_all([task, crit])
        db.flush()

        def mk(path):
            g = Generator(slug=f"g-{uuid.uuid4().hex[:8]}", name="TRELLIS (fal)")
            db.add(g)
            db.flush()
            o = ModelOutput(
                task_id=task.id,
                generator_id=g.id,
                asset_path=path,
                asset_format="glb",
                source="api:fal:trellis",
            )
            db.add(o)
            db.flush()
            return o

        oa, ob = mk(asset_a), mk(asset_b)
        comp = Comparison(
            task_id=task.id,
            criterion_id=crit.id,
            output_a_id=oa.id,
            output_b_id=ob.id,
            is_gold=True,
            session_id="s",
        )
        db.add(comp)
        db.flush()
        payload = main._serialize(comp, task, crit, oa, ob)
        out_payload = main._serialize_output(oa)
        return payload, out_payload, oa.id, ob.id


def test_serialize_url_hides_gold_and_asset_path():
    payload, _out, oa_id, ob_id = _seed_pair("gold/task19__bad.glb", "trellis-rose-5.glb")
    for side, oid in (("a", oa_id), ("b", ob_id)):
        url = payload[side]["url"]
        assert "__bad" not in url, f"{side} url leaks the gold decoy: {url}"
        assert "gold/" not in url and "trellis" not in url, f"{side} url leaks asset_path: {url}"
        assert url == f"/media/o/{oid}.glb"


def test_serialize_output_url_is_opaque():
    _payload, out, oa_id, _ob = _seed_pair("gold/task19__bad.glb", "x.glb")
    assert out["url"] == f"/media/o/{oa_id}.glb"
    assert "__bad" not in out["url"]


def test_media_route_serves_bytes_and_404s_unknown():
    init_db()
    rel = f"test_media/{uuid.uuid4().hex}.glb"
    dest = config.ASSET_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"glTF-fake-bytes")
    try:
        with SessionLocal() as db:
            t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
            g = Generator(slug=f"g-{uuid.uuid4().hex[:8]}", name="g")
            db.add_all([t, g])
            db.flush()
            o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path=rel, asset_format="glb")
            db.add(o)
            db.commit()
            oid = o.id
        r = client.get(f"/media/o/{oid}.glb")
        assert r.status_code == 200
        assert r.content == b"glTF-fake-bytes"
        assert r.headers["content-type"].startswith("model/gltf-binary")
        assert client.get("/media/o/99999999.glb").status_code == 404
    finally:
        dest.unlink(missing_ok=True)
