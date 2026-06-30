"""Tests for the pure seams of the Mode-C browser labeler (scripts/label_server.py).

The FastAPI wiring is exercised by the real-execution smoke run in the session; here we
test the resumable label store: load, merge-seed, next-unlabeled, progress, round-trip."""

from __future__ import annotations

import csv

import pytest

from scripts.label_server import (
    load_sample,
    load_store,
    merge_seed,
    next_unlabeled,
    progress,
    write_store,
)

SAMPLE_FIELDS = ["output_id", "trait_key", "trait_class", "taxon", "expected", "contact_sheet"]


def _write_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _sample(tmp_path):
    rows = [
        {
            "output_id": "1",
            "trait_key": "a",
            "trait_class": "color",
            "taxon": "Rosa",
            "expected": "red",
            "contact_sheet": "/r/1.png",
        },
        {
            "output_id": "2",
            "trait_key": "b",
            "trait_class": "color",
            "taxon": "Rosa",
            "expected": "round",
            "contact_sheet": "/r/2.png",
        },
        {
            "output_id": "3",
            "trait_key": "c",
            "trait_class": "habit",
            "taxon": "Zea",
            "expected": "tall",
            "contact_sheet": "/r/3.png",
        },
    ]
    p = tmp_path / "sample.csv"
    _write_csv(p, rows, SAMPLE_FIELDS)
    return p


def test_load_sample_coerces_output_id(tmp_path):
    rows = load_sample(_sample(tmp_path))
    assert rows[0]["output_id"] == 1
    assert rows[0]["trait_class"] == "color"
    assert len(rows) == 3


def test_load_store_missing_is_empty(tmp_path):
    assert load_store(tmp_path / "nope.csv") == {}


def test_next_unlabeled_skips_labeled(tmp_path):
    rows = load_sample(_sample(tmp_path))
    store = {(1, "a"): {"human_verdict": "absent", "note": ""}}
    assert next_unlabeled(rows, store, after=-1) == 1  # row 0 labeled, next is idx 1
    assert next_unlabeled(rows, store, after=1) == 2


def test_next_unlabeled_none_when_complete(tmp_path):
    rows = load_sample(_sample(tmp_path))
    store = {
        (r["output_id"], r["trait_key"]): {"human_verdict": "absent", "note": ""} for r in rows
    }
    assert next_unlabeled(rows, store, after=-1) is None


def test_progress_counts_per_class(tmp_path):
    rows = load_sample(_sample(tmp_path))
    store = {(1, "a"): {"human_verdict": "present_correct", "note": ""}}
    pr = progress(rows, store)
    assert pr["labeled"] == 1
    assert pr["total"] == 3
    assert pr["per_class"]["color"] == {"labeled": 1, "total": 2}
    assert pr["per_class"]["habit"] == {"labeled": 0, "total": 1}


def test_write_then_load_store_roundtrip(tmp_path):
    rows = load_sample(_sample(tmp_path))
    store = {
        (1, "a"): {"human_verdict": "present_correct", "note": "nice"},
        (3, "c"): {"human_verdict": "absent", "note": "not a plant"},
    }
    store_csv = tmp_path / "filled.csv"
    write_store(store_csv, rows, store)
    back = load_store(store_csv)
    assert back[(1, "a")]["human_verdict"] == "present_correct"
    assert back[(3, "c")]["note"] == "not a plant"
    assert (2, "b") not in back  # unlabeled rows are not written
    # the written file is ingest-shaped
    got = list(csv.DictReader(open(store_csv)))
    assert "human_verdict" in got[0] and "trait_class" in got[0]


def test_merge_seed_pulls_nonblank_valid(tmp_path):
    rows = load_sample(_sample(tmp_path))
    seed = [
        {"output_id": "1", "trait_key": "a", "human_verdict": "absent"},
        {"output_id": "2", "trait_key": "b", "human_verdict": ""},  # blank → ignored
    ]
    seed_csv = tmp_path / "seed.csv"
    _write_csv(seed_csv, seed, ["output_id", "trait_key", "human_verdict"])
    store = {}
    added = merge_seed(store, seed_csv, rows)
    assert added == 1
    assert store[(1, "a")]["human_verdict"] == "absent"


def test_merge_seed_invalid_raises(tmp_path):
    rows = load_sample(_sample(tmp_path))
    seed = [{"output_id": "1", "trait_key": "a", "human_verdict": "maybe"}]
    seed_csv = tmp_path / "seed.csv"
    _write_csv(seed_csv, seed, ["output_id", "trait_key", "human_verdict"])
    with pytest.raises(ValueError, match="invalid"):
        merge_seed({}, seed_csv, rows)
