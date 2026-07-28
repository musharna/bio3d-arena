import pytest

from app.database import SessionLocal, init_db
from app.fidelity import fidelity_scorecard
from app.models import (
    Category,
    Completeness,
    Generator,
    Metric,
    ModelOutput,
    Task,
    TraitRubric,
    TraitScore,
)
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


@pytest.fixture(autouse=True)
def _cleanup_fidtest():
    """The suite shares one session DB (conftest resets only for commission tests), so these
    tests must delete every row they COMMIT or they pollute later global-scanning tests
    (e.g. the trait board). Seeded artifacts are tagged by the ``FidTest-`` taxon/title."""
    yield
    with SessionLocal() as db:
        task_ids = [t.id for t in db.query(Task).filter(Task.title.like("FidTest-%")).all()]
        if task_ids:
            out_ids = [
                o.id for o in db.query(ModelOutput).filter(ModelOutput.task_id.in_(task_ids)).all()
            ]
            if out_ids:
                for model in (Completeness, Metric, TraitScore):
                    db.query(model).filter(model.output_id.in_(out_ids)).delete(
                        synchronize_session=False
                    )
                db.query(ModelOutput).filter(ModelOutput.id.in_(out_ids)).delete(
                    synchronize_session=False
                )
            db.query(TraitRubric).filter(TraitRubric.task_id.in_(task_ids)).delete(
                synchronize_session=False
            )
            db.query(Task).filter(Task.id.in_(task_ids)).delete(synchronize_session=False)
        cascade_delete(db, Generator, Generator.slug.like("%-FidTest-%"))
        db.commit()


def _get_or_create_gen(db, slug, para):
    g = db.query(Generator).filter_by(slug=slug).one_or_none()
    if g is None:
        g = Generator(slug=slug, name=slug, kind="model", paradigm=para)
        db.add(g)
        db.flush()
    return g


def _seed(db, taxon):
    """Seed one taxon block on the SHARED test DB (no per-test rollback): 2 image_recon gens,
    1 procedural_expert, 1 capture_scan (reference). Unique slugs + taxon keep it isolated;
    fidelity_scorecard reads the whole DB, so assertions scope to `taxon`."""
    cat = db.query(Category).filter_by(slug="fidtest-cat").one_or_none()
    if cat is None:
        cat = Category(slug="fidtest-cat", name="Fidelity test")
        db.add(cat)
        db.flush()
    task = Task(category_id=cat.id, title=taxon, prompt="p", active=True)
    db.add(task)
    db.flush()
    db.add(TraitRubric(task_id=task.id, taxon=taxon, traits_json="[]"))
    db.flush()
    gens = {
        key: _get_or_create_gen(db, f"{key}-{taxon}", para)
        for key, para in [
            ("recon-a", "image_recon"),
            ("recon-b", "image_recon"),
            ("lpy", "procedural_expert"),
            ("scan", "capture_scan"),
        ]
    }

    def mo(g):
        o = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            title="o",
            asset_path="a.glb",
            asset_format="glb",
            source="x",
            is_gold=False,
        )
        db.add(o)
        db.flush()
        return o.id

    ids = {
        "a1": mo(gens["recon-a"]),
        "a2": mo(gens["recon-a"]),
        "b": mo(gens["recon-b"]),
        "lpy": mo(gens["lpy"]),
        "scan": mo(gens["scan"]),
    }
    for oid, category, score in [
        (ids["a1"], "complete", 1.0),
        (ids["a2"], "complete", 0.9),
        (ids["b"], "fragment", 0.2),
        (ids["lpy"], "partial-organism", 0.6),
        (ids["scan"], "complete", 1.0),
    ]:
        db.add(Completeness(output_id=oid, category=category, score=score))
    db.add(Metric(output_id=ids["a1"], fscore=0.5, chamfer=0.1, status="ok"))
    db.add(TraitScore(output_id=ids["a1"], botanical_accuracy=0.7, n_scored=5, n_total=5))
    db.commit()
    return ids


def test_scorecard_ranks_by_completeness_and_separates_reference():
    taxon = "FidTest-Maize-agg"
    with SessionLocal() as db:
        _seed(db, taxon)
        sc = fidelity_scorecard(db)
    block = next(t for t in sc["taxa"] if t["taxon"] == taxon)
    paras = [r["paradigm"] for r in block["rows"]]
    assert "capture_scan" not in paras  # reference is not a ranked competitor
    assert [r["paradigm"] for r in block["reference"]] == ["capture_scan"]
    ir = next(r for r in block["rows"] if r["paradigm"] == "image_recon")
    assert ir["n"] == 3  # recon-a x2 + recon-b
    assert abs(ir["completeness"]["pct_complete"] - 2 / 3) < 1e-9  # 2 complete of 3
    assert block["rows"][0]["paradigm"] == "image_recon"  # 0.667 > procedural_expert 0.0
    assert ir["geometry"]["n"] == 1 and ir["trait"]["n"] == 1  # sparse axes = true coverage
    assert ir["best_model"]["score"] == 1.0  # recon-a's best output


def test_null_valued_axis_rows_excluded_from_coverage():
    # A metric/trait row can exist with a NULL value (failed/unscored read). Such rows must NOT
    # inflate the axis coverage count `n` (else the board shows n>0 with a None mean -> crash).
    taxon = "FidTest-NullAxis"
    with SessionLocal() as db:
        ids = _seed(db, taxon)
        db.add(Metric(output_id=ids["b"], fscore=None, chamfer=None, status="error"))
        db.add(TraitScore(output_id=ids["b"], botanical_accuracy=None, n_scored=0, n_total=5))
        db.commit()
        sc = fidelity_scorecard(db)
    block = next(t for t in sc["taxa"] if t["taxon"] == taxon)
    ir = next(r for r in block["rows"] if r["paradigm"] == "image_recon")
    assert (
        ir["geometry"]["n"] == 1 and ir["geometry"]["mean_fscore"] == 0.5
    )  # only a1, NULL b excluded
    assert ir["trait"]["n"] == 1 and ir["trait"]["mean_accuracy"] == 0.7


def test_unbackfilled_paradigm_generator_excluded():
    # A generator whose paradigm is still "" (not yet run through backfill_paradigms) must NOT
    # appear as a blank-labeled ranked row — treat "" like a missing paradigm.
    taxon = "FidTest-Unbackfilled"
    with SessionLocal() as db:
        _seed(db, taxon)
        g = Generator(slug=f"unbackfilled-{taxon}", name="unbackfilled", kind="model", paradigm="")
        db.add(g)
        db.flush()
        task = db.query(Task).filter_by(title=taxon).one()
        o = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            title="o",
            asset_path="a.glb",
            asset_format="glb",
            source="x",
            is_gold=False,
        )
        db.add(o)
        db.flush()
        db.add(Completeness(output_id=o.id, category="complete", score=1.0))
        db.commit()
        sc = fidelity_scorecard(db)
    block = next(t for t in sc["taxa"] if t["taxon"] == taxon)
    all_paras = [r["paradigm"] for r in block["rows"] + block["reference"]]
    assert "" not in all_paras  # blank-paradigm generator excluded from the board


def test_empty_state_when_no_completeness(monkeypatch):
    from app import fidelity as fidmod

    monkeypatch.setattr(fidmod.service, "completeness_rows", lambda db: [])
    with SessionLocal() as db:
        sc = fidelity_scorecard(db)
    assert sc["taxa"] == []
    assert any(a["primary"] for a in sc["axes_meta"])


def test_api_fidelity_json_shape():
    from fastapi.testclient import TestClient

    from app.main import app

    taxon = "FidTest-API"
    with SessionLocal() as db:
        _seed(db, taxon)
    r = TestClient(app).get("/api/fidelity.json")
    assert r.status_code == 200
    data = r.json()
    assert [a["key"] for a in data["axes_meta"]] == ["completeness", "geometry", "trait"]
    assert any(t["taxon"] == taxon for t in data["taxa"])


def test_fidelity_page_populated():
    from fastapi.testclient import TestClient

    from app.main import app

    taxon = "FidTest-Page"
    with SessionLocal() as db:
        _seed(db, taxon)
    r = TestClient(app).get("/fidelity")
    assert r.status_code == 200
    assert taxon in r.text
    assert "validated" in r.text.lower() and "experimental" in r.text.lower()  # axis badges


def test_fidelity_page_empty_state(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as mainmod

    monkeypatch.setattr(
        mainmod.fidelity,
        "fidelity_scorecard",
        lambda db: {
            "axes_meta": [{"key": "completeness", "label": "L", "badge": "b", "primary": True}],
            "taxa": [],
        },
    )
    r = TestClient(mainmod.app).get("/fidelity")
    assert r.status_code == 200
    assert "no completeness data" in r.text.lower()
