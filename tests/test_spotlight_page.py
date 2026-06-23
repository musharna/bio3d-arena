# tests/test_spotlight_page.py
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app import ingest, spotlight
from app.assets_gen import build_asset
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Category, Metric, Task
from app.seed import RECON_SPECIES, VOLUMETRIC_SUBJECTS


def setup_module(_m):
    init_db()


def test_every_spotlight_task_title_matches_a_seeded_species():
    """Each curated SPOTLIGHTS entry must point at a real seeded recon or volumetric subject —
    guards against title drift between the spotlight registry and the seed (e.g. the maize/Zea
    mays entry, the barley-MRI volumetric entry)."""
    seeded = {f"{sci} — single-image → 3D reconstruction" for _slug, sci, _d in RECON_SPECIES}
    seeded |= {title for title, _prompt in VOLUMETRIC_SUBJECTS}
    for s in spotlight.SPOTLIGHTS:
        assert s["task_title"] in seeded, (
            f"{s['slug']} -> {s['task_title']!r} has no seeded subject"
        )


def test_maize_spotlight_registered_and_featured():
    """Maize is the second featured crop spotlight (the Xfrog AG20 Zea mays subject)."""
    maize = spotlight.find_spotlight("maize")
    assert maize is not None, "maize spotlight not registered"
    assert maize["task_title"] == "Zea mays — single-image → 3D reconstruction"
    assert maize["featured"] is True


def _glb_bytes(seed: int = 1) -> bytes:
    tmp = Path(tempfile.mkdtemp(prefix="bio3d_sptl_")) / "stub.glb"
    build_asset("flower", seed, tmp)
    return tmp.read_bytes()


def _seed_subject(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="Spotlight Test Subject", prompt="p")
    db.add(task)
    db.flush()
    for gslug, ch in [("m-a", 0.12), ("m-b", 0.18)]:
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug=gslug,
            data=_glb_bytes(),
            ext="glb",
            title=f"out {gslug}",
        )
        db.add(
            Metric(
                output_id=out.id,
                status="ok",
                chamfer=ch,
                gt_band_lo=0.10,
                gt_band_hi=0.14,
                coverage=0.8,
                fscore=0.8,
            )
        )
    db.commit()
    return task


def test_build_spotlight_assembles_models(monkeypatch):
    db = SessionLocal()
    try:
        _seed_subject(db)
        monkeypatch.setattr(
            spotlight,
            "SPOTLIGHTS",
            [
                {
                    "slug": "test",
                    "task_title": "Spotlight Test Subject",
                    "featured": True,
                    "order": 0,
                    "blurb": "b",
                    "reference_image": None,
                },
            ],
        )
        data = spotlight.build_spotlight(db, "test")
        assert data is not None
        assert len(data["models"]) == 2
        # the 0.18 model must carry a shape flag; the 0.12 model must be ok
        flags = {m["generator"]: [k for k, _ in m["flags"]] for m in data["models"]}
        assert "shape" in flags["m-b"]
        assert "ok" in flags["m-a"]
        assert data["models"][0]["provenance"]["source"] == "bio3d-arena"
    finally:
        db.close()


def _seed_unscored_subject(db):
    """Seed a curated subject with one output that has NO Metric row."""
    cat = db.query(Category).filter_by(slug="plants-u").first() or Category(
        slug="plants-u", name="Plants Unscored"
    )
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="Spotlight Unscored Subject", prompt="p")
    db.add(task)
    db.flush()
    out, _ = ingest.register_output(
        db,
        task_id=task.id,
        generator_slug="m-unscored",
        data=_glb_bytes(seed=7),
        ext="glb",
        title="out unscored",
    )
    # Deliberately no Metric row created.
    db.commit()
    return task


_UNSCORED_SPOTLIGHT = [
    {
        "slug": "unscored-test",
        "task_title": "Spotlight Unscored Subject",
        "featured": False,
        "order": 99,
        "blurb": "unscored regression test",
        "reference_image": None,
    }
]


def test_unscored_output_no_500(monkeypatch):
    """FIX-1 regression: output with no Metric row must not 500 the page."""
    db = SessionLocal()
    try:
        _seed_unscored_subject(db)
        monkeypatch.setattr(spotlight, "SPOTLIGHTS", _UNSCORED_SPOTLIGHT)
        # Verify build_spotlight populates None metrics and unscored flag.
        data = spotlight.build_spotlight(db, "unscored-test")
        assert data is not None
        assert len(data["models"]) == 1
        model = data["models"][0]
        assert model["metrics"]["chamfer"] is None
        assert any(kind == "unscored" for kind, _ in model["flags"])
    finally:
        db.close()

    # Verify the HTTP route returns 200, not 500.
    monkeypatch.setattr(spotlight, "SPOTLIGHTS", _UNSCORED_SPOTLIGHT)
    client = TestClient(app)
    page = client.get("/spotlight/unscored-test")
    assert page.status_code == 200
    assert "—" in page.text


def test_spotlight_route_renders(monkeypatch):
    db = SessionLocal()
    try:
        _seed_subject(db)
    finally:
        db.close()
    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [
            {
                "slug": "test",
                "task_title": "Spotlight Test Subject",
                "featured": True,
                "order": 0,
                "blurb": "b",
                "reference_image": None,
            },
        ],
    )
    client = TestClient(app)
    assert client.get("/spotlight").status_code == 200
    page = client.get("/spotlight/test")
    assert page.status_code == 200
    assert "m-a" in page.text and "m-b" in page.text
    assert client.get("/spotlight/does-not-exist").status_code == 404


def test_reference_image_resolved_to_served_url(monkeypatch):
    from app.main import app
    from app.storage import get_storage

    db = SessionLocal()
    try:
        _seed_subject(db)  # existing helper in this module
    finally:
        db.close()
    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [
            {
                "slug": "test",
                "task_title": "Spotlight Test Subject",
                "featured": True,
                "order": 0,
                "blurb": "b",
                "reference_image": "reference/tomato_ref.jpg",
                "reference_credit": "Photo: Someone, CC-BY-SA-4.0",
            },
        ],
    )
    page = TestClient(app).get("/spotlight/test")
    assert page.status_code == 200
    # url_for prefixes with the static mount (verified: LocalStorageBackend ->
    # "/assets/reference/tomato_ref.jpg"). The RED state renders the RAW relative path
    # ("reference/tomato_ref.jpg"); GREEN renders the resolved served URL.
    expected = get_storage().url_for("reference/tomato_ref.jpg")
    assert 'class="ref-img"' in page.text  # the reference <img> rendered
    assert f'src="{expected}"' in page.text  # resolved served URL, not the raw relative path
    assert "Photo: Someone, CC-BY-SA-4.0" in page.text  # attribution credit shown (CC compliance)
