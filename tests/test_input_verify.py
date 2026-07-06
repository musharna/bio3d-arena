import json

from app import input_verify
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_verify_flags_mismatch(monkeypatch):
    monkeypatch.setattr(
        "app.reference_qa.species_matches",
        lambda bundle, png, *, claimed_taxon, panel, min_margin=0.0: {
            "ok": False,
            "top": "Zea mays",
            "prob": 0.9,
            "margin": 0.8,
        },
    )
    r = input_verify.verify_input_subject(
        object(),
        b"x",
        claimed_taxon="Solanum lycopersicum",
        panel=["Solanum lycopersicum", "Zea mays"],
    )
    assert r["ok"] is False and r["top"] == "Zea mays"


def test_verify_passes_match(monkeypatch):
    monkeypatch.setattr(
        "app.reference_qa.species_matches",
        lambda bundle, png, *, claimed_taxon, panel, min_margin=0.0: {
            "ok": True,
            "top": claimed_taxon,
            "prob": 0.9,
            "margin": 0.8,
        },
    )
    r = input_verify.verify_input_subject(object(), b"x", claimed_taxon="Solanum lycopersicum")
    assert r["ok"] is True


def test_scan_and_flag_records_non_hiding_advisory(monkeypatch):
    import uuid

    from app import flags

    tag = uuid.uuid4().hex[:8]
    # A mismatch verdict for every output.
    monkeypatch.setattr(
        input_verify,
        "verify_input_subject",
        lambda bundle, png, *, claimed_taxon, panel=None, min_margin=0.0: {
            "ok": False,
            "top": "Zea mays",
            "prob": 0.9,
            "margin": 0.8,
        },
    )
    with SessionLocal() as db:
        cat = Category(slug=f"iv-{tag}", name="c")
        g = Generator(slug=f"iv-g-{tag}", name="g", kind="model", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        t = Task(
            category_id=cat.id, title=f"Solanum lycopersicum — recon {tag}", prompt="p", active=True
        )
        db.add(t)
        db.flush()
        o = ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="a.glb",
            source="bio3d-arena",
            meta_json=json.dumps({"input_image": "reference/tomato_ref.jpg"}),
        )
        db.add(o)
        db.flush()

        triage = input_verify.scan_and_flag(
            db,
            bundle=object(),
            resolve_png=lambda rel: b"\xff\xd8\xff jpeg",
            taxon_of=lambda out: "Solanum lycopersicum",
            apply=True,
        )
        assert any(x["output_id"] == o.id and x["reads_as"] == "Zea mays" for x in triage)
        db.refresh(o)
        assert o.hidden_at is None  # advisory: never auto-hides
        assert flags.distinct_flag_count(db, o.id) == 1

        # cleanup (apply=True committed)
        from app.models import OutputFlag

        for f in db.execute(
            __import__("sqlalchemy").select(OutputFlag).where(OutputFlag.output_id == o.id)
        ).scalars():
            db.delete(f)
        db.delete(o)
        db.delete(t)
        db.delete(g)
        db.delete(cat)
        db.commit()
