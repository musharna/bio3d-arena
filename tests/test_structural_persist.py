# tests/test_structural_persist.py
from __future__ import annotations

import uuid

import trimesh

from app import structural
from app.admissibility import Verdict
from app.config import ASSET_DIR
from app.database import SessionLocal, init_db
from app.models import Admissibility, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _output(db, rel_asset):
    g = Generator(slug=f"st-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"st-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path=rel_asset, asset_format="glb")
    db.add(o)
    db.commit()
    return o.id


def _write_asset(rel, mesh):
    p = ASSET_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(p)


def test_upsert_overwrites():
    with SessionLocal() as db:
        oid = _output(db, "st/x.glb")
        structural.upsert_verdict(
            db, oid, "structural", Verdict(False, "empty", {}), "structural-v1"
        )
        structural.upsert_verdict(db, oid, "structural", Verdict(True, "", {}), "structural-v1")
        db.commit()
        rows = db.query(Admissibility).filter_by(output_id=oid, predicate="structural").all()
        assert len(rows) == 1 and rows[0].admit is True


def test_evaluate_outputs_and_rejected():
    with SessionLocal() as db:
        rel_bad = f"st/{uuid.uuid4().hex}.glb"
        rel_good = f"st/{uuid.uuid4().hex}.glb"
        tri = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
        _write_asset(rel_bad, tri)
        _write_asset(rel_good, trimesh.creation.box((1, 1, 1)))
        bad = _output(db, rel_bad)
        good = _output(db, rel_good)
        res = structural.evaluate_outputs(db, [bad, good])
        db.commit()
        assert res["scored"] == 2
        rejected = structural.StructuralPredicate().rejected_output_ids(db)
        assert bad in rejected and good not in rejected


def test_enumerate_skips_current_version():
    with SessionLocal() as db:
        oid = _output(db, "st/y.glb")
        assert oid in structural.enumerate_structural_work(db)
        structural.upsert_verdict(db, oid, "structural", Verdict(True, "", {}), structural.VERSION)
        db.commit()
        assert oid not in structural.enumerate_structural_work(db)


def test_evaluate_outputs_fail_loud_per_output():
    with SessionLocal() as db:
        missing = _output(db, "st/does-not-exist.glb")  # no file on disk
        rel_good = f"st/{uuid.uuid4().hex}.glb"
        _write_asset(rel_good, trimesh.creation.box((1, 1, 1)))
        good = _output(db, rel_good)
        res = structural.evaluate_outputs(db, [missing, good])
        db.commit()
        # A missing/unreadable asset yields a reject verdict (not a crash); the loop continues.
        assert res["scored"] >= 1
        assert missing in structural.StructuralPredicate().rejected_output_ids(db)


def test_evaluate_outputs_reads_via_storage_backend(monkeypatch):
    """S3-safety proof (whole-branch review Fix 1): evaluate_outputs must resolve the asset
    through get_storage().read(), never a raw ASSET_DIR filesystem path — on the S3 backend
    there is no local copy to read. Point the output at a path with NO file on disk anywhere,
    then monkeypatch get_storage() to hand back degenerate-mesh bytes regardless of path.
    If evaluate_outputs still depended on the raw filesystem, this asset would be
    "unreadable" (missing file); instead it must be rejected on genuine geometry grounds,
    proving the read went through the (fake) storage backend."""

    class FakeStorage:
        def read(self, rel_path):
            tri = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
            return tri.export(file_type="glb")

    monkeypatch.setattr(structural, "get_storage", lambda: FakeStorage())
    with SessionLocal() as db:
        oid = _output(db, f"nowhere/{uuid.uuid4().hex}.glb")  # never written to ASSET_DIR
        res = structural.evaluate_outputs(db, [oid])
        db.commit()
        assert res["scored"] == 1
        row = db.query(Admissibility).filter_by(output_id=oid, predicate="structural").one()
        assert row.admit is False
        assert row.reason != "unreadable"  # rejected on geometry, not a missing-file fallback
        assert oid in structural.StructuralPredicate().rejected_output_ids(db)


def test_default_rubric_includes_structural_rejects():
    """Carry-forward from Task 2 review: after the direct (unguarded) import of
    StructuralPredicate in admissibility._registry(), the DEFAULT rubric must resolve
    structural too, not just completeness."""
    from app import admissibility

    with SessionLocal() as db:
        rel_bad = f"st/{uuid.uuid4().hex}.glb"
        tri = trimesh.Trimesh(vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces=[[0, 1, 2]])
        _write_asset(rel_bad, tri)
        bad = _output(db, rel_bad)
        structural.evaluate_outputs(db, [bad])
        db.commit()
        rejected = admissibility.non_admitted_output_ids(db)  # default rubric
        assert bad in rejected
