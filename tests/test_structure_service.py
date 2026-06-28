"""Mode-B organ-structure fidelity service — fake-scorer unit tests.

Fakes mirror AgriGen's live /score_structure response. The real round-trip is exercised by
the live smoke (scripts/smoke_score_structure.py, skipped when the service is down); these
inject a fake scorer so the resolve/map/store logic is covered without the microservice.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from app import recon_service, structure_service
from app.database import SessionLocal, init_db
from app.models import Category, Generator, Metric, ModelOutput, OrganMetric, ReconTask, Task
from app.recon_client import ScorerError
from app.storage import get_storage


def setup_module(_module):
    init_db()
    get_storage().save("seed/x.glb", b"glTF-stub-bytes")


def fake_structure_card(species="zea_mays", fidelity=1.0, note=None) -> dict:
    """A live-shaped /score_structure response with a tunable fidelity."""
    if note is not None or fidelity is None:
        return {
            "species": species,
            "botanical_fidelity": None,
            "n_attributes": 0,
            "note": note or "no botanical reference",
        }
    return {
        "species": species,
        "botanical_fidelity": fidelity,
        "n_attributes": 2,
        "attributes": {
            "leaf_axis_count": {
                "extracted": 18,
                "expected": "16-20",
                "status": "PASS",
                "graded": 1.0,
                "src": "OSU Ohioline",
            },
        },
    }


def _mk_output(
    db,
    key,
    *,
    source="procedural:agrigen",
    species_slug="zea_mays",
    variant="maize",
    asset="seed/x.glb",
):
    cat = Category(slug=f"c-{key}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"t-{key}", prompt="p")
    gen = Generator(slug=f"g-{key}", name=f"M-{key}")
    db.add_all([task, gen])
    db.flush()
    if species_slug is not None:
        db.add(ReconTask(task_id=task.id, species_slug=species_slug, species_name=species_slug))
    out = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        asset_path=asset,
        asset_format="glb",
        source=source,
        meta_json=json.dumps({"variant": variant}),
    )
    db.add(out)
    db.flush()
    return out


def test_procedural_output_scored_and_stored():
    db = SessionLocal()
    try:
        out = _mk_output(db, "ok")
        posted = {}

        def scorer(record):
            posted.update(record)
            return fake_structure_card(species="zea_mays", fidelity=1.0)

        m = structure_service.score_and_store(db, out, scorer=scorer)
        assert m is not None and m.status == "scored"
        assert m.botanical_fidelity == 1.0
        assert m.species_slug == "zea_mays"
        assert m.n_attributes == 2
        assert json.loads(m.attributes)["leaf_axis_count"]["status"] == "PASS"
        # The seed-PD record for zea_mays was posted (resolved via the ReconTask slug).
        assert posted["species"] == "zea_mays"
        assert posted["leaf_axis_count"] == 18
    finally:
        db.close()


def test_recon_output_is_na_no_row():
    """Non-procedural (recon) outputs are N/A on the organ axis — no row, render '—'."""
    db = SessionLocal()
    try:
        out = _mk_output(db, "recon", source="recon:trellis")
        m = structure_service.score_and_store(db, out, scorer=lambda r: fake_structure_card())
        assert m is None
        rows = (
            db.execute(select(OrganMetric).where(OrganMetric.output_id == out.id)).scalars().all()
        )
        assert rows == []
    finally:
        db.close()


def test_pine_structural_gap_is_scored_zero_not_laundered():
    db = SessionLocal()
    try:
        out = _mk_output(db, "pine", species_slug="pinus_sylvestris", variant="pine")
        posted = {}

        def scorer(record):
            posted.update(record)
            return fake_structure_card(species="pinus_sylvestris", fidelity=0.0)

        m = structure_service.score_and_store(db, out, scorer=scorer)
        assert m is not None and m.status == "scored"  # 0.0 is a valid finding, displayed
        assert m.botanical_fidelity == 0.0
        # No laundering: needles_per_fascicle is posted as None, not a hand-typed 2.
        assert posted["needles_per_fascicle"] is None
    finally:
        db.close()


def test_uncovered_species_via_sidecar_is_honest_na():
    db = SessionLocal()
    try:
        # A sidecar declaring an un-referenced species; no ReconTask slug.
        get_storage().save(
            "seed/rose__structure.json",
            json.dumps({"species": "rosa_canina", "primary_branch_count": 5}).encode(),
        )
        out = _mk_output(
            db,
            "rose",
            source="procedural:agrigen",
            species_slug=None,
            variant="rose",
            asset="seed/rose.glb",
        )
        m = structure_service.score_and_store(
            db,
            out,
            scorer=lambda r: fake_structure_card(
                species="rosa_canina", note="no botanical reference"
            ),
        )
        assert m is not None and m.status == "no_reference"
        assert m.botanical_fidelity is None
        assert "no botanical reference" in m.note
    finally:
        db.close()


def test_offline_records_error_not_crash():
    db = SessionLocal()
    try:
        out = _mk_output(db, "offline")

        def boom(record):
            raise ScorerError("structure scorer at http://x: connect refused")

        m = structure_service.score_and_store(db, out, scorer=boom)
        assert m is not None and m.status == "error"
        assert "connect refused" in m.detail
        assert m.botanical_fidelity is None
    finally:
        db.close()


def test_score_upserts_not_duplicates():
    db = SessionLocal()
    try:
        out = _mk_output(db, "upsert")
        structure_service.score_and_store(
            db, out, scorer=lambda r: fake_structure_card(fidelity=1.0)
        )
        structure_service.score_and_store(
            db, out, scorer=lambda r: fake_structure_card(fidelity=0.0)
        )
        db.commit()
        rows = (
            db.execute(select(OrganMetric).where(OrganMetric.output_id == out.id)).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].botanical_fidelity == 0.0  # latest wins
    finally:
        db.close()


def test_rescore_all_skips_na_counts_scored():
    db = SessionLocal()
    try:
        _mk_output(db, "batch-proc", source="procedural:agrigen", species_slug="zea_mays")
        _mk_output(db, "batch-recon", source="recon:trellis", species_slug="zea_mays")
        db.commit()
        detail = structure_service.rescore_all(
            db, scorer=lambda r: fake_structure_card(fidelity=1.0)
        )
        assert detail["scored"] >= 1
        # recon outputs (+ any other non-procedural seed rows) are skipped, never errored.
        assert detail["errors"] == 0
    finally:
        db.close()


def test_board_surfaces_organ_fidelity_beside_chamfer():
    """The Mode-B method board carries organ_fidelity for structure-known rows and None
    (→ '—') for recon-only methods."""
    db = SessionLocal()
    try:
        cat = Category(slug="c-board", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-board", prompt="p")
        # Unique names so the DB-wide display-name disambiguation (shared names get a slug
        # suffix) never fires for this isolated board test.
        proc = Generator(slug="g-proc-board", name="AgriGen-board")
        recon = Generator(slug="g-recon-board", name="TRELLIS-board")
        db.add_all([task, proc, recon])
        db.flush()
        db.add(ReconTask(task_id=task.id, species_slug="zea_mays", species_name="zea_mays"))

        proc_out = ModelOutput(
            task_id=task.id,
            generator_id=proc.id,
            asset_path="seed/x.glb",
            asset_format="glb",
            source="procedural:agrigen",
        )
        recon_out = ModelOutput(
            task_id=task.id,
            generator_id=recon.id,
            asset_path="seed/x.glb",
            asset_format="glb",
            source="recon:trellis",
        )
        db.add_all([proc_out, recon_out])
        db.flush()
        # Both have a chamfer (both scored vs GT) so both appear on the board.
        db.add(
            Metric(
                output_id=proc_out.id, chamfer=0.20, status="ok", gt_band_lo=0.1, gt_band_hi=0.14
            )
        )
        db.add(
            Metric(
                output_id=recon_out.id, chamfer=0.05, status="ok", gt_band_lo=0.1, gt_band_hi=0.14
            )
        )
        # Only the procedural method has an organ score.
        db.add(
            OrganMetric(
                output_id=proc_out.id,
                species_slug="zea_mays",
                botanical_fidelity=1.0,
                n_attributes=2,
                attributes="{}",
                status="scored",
            )
        )
        db.commit()

        board = recon_service.recon_method_leaderboard(db, task.id)
        by_name = {r["generator"]: r for r in board}
        assert by_name["AgriGen-board"]["organ_fidelity"] == 1.0
        assert by_name["TRELLIS-board"]["organ_fidelity"] is None  # recon → N/A on organ axis
    finally:
        db.close()
