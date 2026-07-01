"""Tests for the four P1/P2 audit fixes (2026-06-28).

FIX 1 (P1-3): /benchmark defaults to first scored task not first task
FIX 2 (P1-5): is_reference_scan + _build_comparison excludes reference outputs
FIX 3 (P1-4): cross_species_summary returns one row per ReconTask with spearman field
FIX 4 (P2-6): spotlight thumbnail placeholder clearly styled (checked via template text)
"""

from __future__ import annotations

import random

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, ReconTask, Task
from app.sourcing import is_reference_scan
from app.storage import get_storage


def setup_module(_module):
    init_db()
    get_storage().save("seed/x.glb", b"glTF-stub-bytes")


# ── FIX 2: is_reference_scan ─────────────────────────────────────────────────


def test_is_reference_scan_true_for_listed_sources():
    assert is_reference_scan("rose-x")
    assert is_reference_scan("ct:rose-x")
    assert is_reference_scan("romi-arabidopsis")
    assert is_reference_scan("mri:ipk-barley-mri")
    assert is_reference_scan("crops3d")
    assert is_reference_scan("plant3d")
    assert is_reference_scan("icrisat-legume")


def test_is_reference_scan_case_insensitive():
    assert is_reference_scan("PLANT3D")
    assert is_reference_scan("ROSE-X")
    assert is_reference_scan("Crops3D")


def test_is_reference_scan_false_for_generative_sources():
    assert not is_reference_scan("api:fal:trellis")
    assert not is_reference_scan("found:sketchfab")
    assert not is_reference_scan("procedural:lpy")
    assert not is_reference_scan("objaverse")
    assert not is_reference_scan("recon:trellis-mv")
    assert not is_reference_scan("bio3d-arena")
    assert not is_reference_scan(None)
    assert not is_reference_scan("")


def test_is_reference_scan_prefix_match():
    # Prefixes should match subpaths too (future-proofing).
    assert is_reference_scan("plant3d-extra")
    assert is_reference_scan("icrisat-legume-v2")


# ── FIX 2: _build_comparison excludes reference-scan outputs ─────────────────


def _mk_seeded_task(db):
    """Return (task, gen_real_a, gen_real_b, gen_ref) with three outputs seeded.
    gen_ref has source=plant3d (a reference scan); the other two are ai."""
    cat = Category(slug="c-fix2-%s" % random.randint(0, 999999), name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="t-fix2", prompt="p", active=True)
    db.add(task)
    db.flush()

    def mk_gen_and_output(slug, source):
        g = Generator(slug="g-fix2-%s" % slug, name="M-%s" % slug)
        db.add(g)
        db.flush()
        o = ModelOutput(
            task_id=task.id,
            generator_id=g.id,
            asset_path="seed/x.glb",
            asset_format="glb",
            source=source,
        )
        db.add(o)
        db.flush()
        return g, o

    ga, oa = mk_gen_and_output("real-a-%d" % random.randint(0, 999999), "api:fal:trellis")
    gb, ob = mk_gen_and_output("real-b-%d" % random.randint(0, 999999), "recon:trellis-mv")
    gc, oc = mk_gen_and_output("ref-%d" % random.randint(0, 999999), "plant3d")
    db.commit()
    return task, oa, ob, oc


def test_build_comparison_never_returns_reference_scan():
    """_build_comparison must not pick a reference-scan output as either side of the pair."""
    from app.main import _build_comparison
    from app.models import Criterion

    # We need the app DB seeded (for criterion etc.) but we also need our custom task.
    # Use a fresh SessionLocal; _build_comparison creates its own picks.
    init_db()
    db = SessionLocal()
    try:
        # Ensure overall criterion exists (needed by _build_comparison).
        overall = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        if overall is None:
            from app.models import Criterion as C

            overall = C(slug="overall", name="Overall")
            db.add(overall)
            db.commit()

        task, oa, ob, oc = _mk_seeded_task(db)
        ref_id = oc.id

        from app.models import Comparison

        seen_ids = set()
        for _ in range(30):
            comp = _build_comparison(db, session_id="test-session")
            if comp is None:
                continue
            # Resolve the picked output IDs from the persisted Comparison row.
            cmp_row = db.get(Comparison, comp["comparison_id"])
            if cmp_row is None:
                continue
            seen_ids.add(cmp_row.output_a_id)
            seen_ids.add(cmp_row.output_b_id)

        # Reference output (plant3d) must never appear in any picked pair.
        assert ref_id not in seen_ids, f"Reference-scan output {ref_id} appeared in the vote pool"
    finally:
        db.close()


# ── FIX 3: cross_species_summary ─────────────────────────────────────────────


def _mk_recon_task(db, suffix):
    cat = Category(slug=f"c-xs-{suffix}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"t-xs-{suffix}", prompt="p")
    db.add(task)
    db.flush()
    rt = ReconTask(task_id=task.id, species_slug=f"sp-{suffix}", species_name=f"Species {suffix}")
    db.add(rt)
    db.flush()
    return task, rt


def test_cross_species_summary_one_row_per_recon_task():
    """cross_species_summary returns one row per ReconTask with required keys."""
    from app import recon_service

    db = SessionLocal()
    try:
        t1, _ = _mk_recon_task(db, "a")
        t2, _ = _mk_recon_task(db, "b")
        db.commit()

        summary = recon_service.cross_species_summary(db)

        task_ids = {row["task_id"] for row in summary}
        assert t1.id in task_ids
        assert t2.id in task_ids

        for row in summary:
            assert "task_id" in row
            assert "species" in row
            assert "spearman" in row
            assert "n_common" in row
            assert "n_methods" in row
            assert "status" in row
    finally:
        db.close()


def test_cross_species_summary_empty_when_no_recon_tasks():
    """cross_species_summary returns an empty list when no ReconTask rows exist."""
    from app import recon_service
    from app.database import Base

    # Use an in-memory DB stub with no ReconTask rows.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    mem_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(mem_engine)
    MemSession = sessionmaker(bind=mem_engine)
    db = MemSession()
    try:
        summary = recon_service.cross_species_summary(db)
        assert summary == []
    finally:
        db.close()


# ── FIX 4: spotlight placeholder (static check) ──────────────────────────────


def test_spotlight_placeholder_text_present():
    """The spotlight template has a no-preview placeholder (not just blank) for missing thumbs."""
    from pathlib import Path

    tmpl = Path(__file__).resolve().parent.parent / "app" / "templates" / "spotlight.html"
    content = tmpl.read_text()
    # Must have a styled no-preview placeholder (not raw blank) for thumbnails that are missing.
    assert "thumb-placeholder" in content
    # The root-cause comment must be present in the template or sourcing.
    # (It's OK if it's only in the CSS — we just verify the placeholder mechanism exists.)


def test_spotlight_no_preview_placeholder_has_css():
    """The CSS for .thumb-placeholder must have a background so it's not blank-white."""
    from pathlib import Path

    css = Path(__file__).resolve().parent.parent / "app" / "static" / "style.css"
    content = css.read_text()
    # After FIX 4, .thumb-placeholder should have background set (not just color/font-size).
    import re

    block_match = re.search(r"\.thumb-placeholder\s*\{([^}]+)\}", content, re.DOTALL)
    assert block_match, ".thumb-placeholder CSS block not found"
    block = block_match.group(1)
    assert "background" in block, (
        ".thumb-placeholder has no background; blank thumbnails look broken"
    )


# ── FIX 1: benchmark defaults to scored task ─────────────────────────────────


def _mk_unscored_task(db, suffix):
    """Task with no Metric rows (no scores yet)."""
    cat = Category(slug=f"c-bm-{suffix}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"t-bm-{suffix}", prompt="p")
    gen = Generator(slug=f"g-bm-{suffix}", name=f"M-bm-{suffix}")
    db.add_all([task, gen])
    db.flush()
    out = ModelOutput(
        task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
    )
    db.add(out)
    db.flush()
    return task, out


def test_benchmark_page_defaults_to_first_scored_task():
    """benchmark_page must pick the first task with ReconTask+ok Metric over unscored tasks."""
    from app import recon_service

    db = SessionLocal()
    try:
        # Unscored task (no Metric) — if chosen first by tasks[0], this would cause "No scores."
        unscored, _ = _mk_unscored_task(db, "us")

        # Scored task: has ReconTask + one ok Metric.
        scored, out_s = _mk_unscored_task(db, "sc")
        rt = ReconTask(task_id=scored.id, species_slug="sp-sc", species_name="Sp SC")
        db.add(rt)
        db.flush()
        recon_service.score_and_store(
            db,
            out_s,
            scorer=lambda b, t: {
                "task_id": "sp-sc",
                "bundle_version": "sha256:abc",
                "metrics": {
                    "chamfer": 0.05,
                    "fscore_at_tau": 0.8,
                    "coverage": 0.7,
                    "matched_gt_index": 0,
                    "params": {
                        "tau": 0.05,
                        "seed": 0,
                        "metric": "v1",
                        "n_points": 1000,
                        "scale_cm": None,
                    },
                },
                "band": {"band_median": 0.10, "band_p75": 0.15, "n_baseline": 3},
                "within_variation": True,
            },
        )
        db.commit()

        # Simulate the benchmark_page default selection logic (extracted helper).
        from app.main import _default_benchmark_task_id

        chosen_id = _default_benchmark_task_id(db)

        assert chosen_id == scored.id, (
            f"Expected scored task {scored.id}, got {chosen_id} — "
            "benchmark_page is defaulting to an unscored task"
        )
    finally:
        db.close()


# ── Visual audit 2026-06-30: nav /dataset link + verified-toggle + None guard ──


def test_dataset_link_in_nav():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert "/dataset" in client.get("/").text  # nav link present on every page


def test_leaderboard_verified_toggle_and_scope():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.get("/leaderboard")
    assert r.status_code == 200 and "verified=true" in r.text  # toggle present
    rv = client.get("/leaderboard?verified=true")
    assert rv.status_code == 200  # verified scope renders


def test_no_literal_none_in_bias_line_when_zero_votes():
    # The bias-line guard must never leak the literal "None" into the rendered
    # tie/bad rates, regardless of whether the shared test DB has prior votes.
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    text = client.get("/leaderboard").text
    assert "tie None" not in text and "bad None" not in text
