# tests/test_rederive_completeness.py
"""Real-execution check for scripts.rederive_completeness against a temp SQLite DB (the script
mutates a DB — a system boundary, so it is exercised end-to-end, not just via the pure derive)."""

import json
import sqlite3

from app.organ_inventory import inventory_for
from scripts.rederive_completeness import _rederive


def _seed(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE completeness (id INTEGER PRIMARY KEY, output_id INT, category TEXT,
                                   score REAL, checklist_json TEXT);
        CREATE TABLE model_output (id INTEGER PRIMARY KEY, task_id INT);
        CREATE TABLE trait_rubric (id INTEGER PRIMARY KEY, task_id INT, taxon TEXT);
        """
    )
    # A dog with every part-TYPE present but the VLM reporting a short leg complement — the exact
    # row the old scorer stamped `malformed`.
    inv = inventory_for("Canis lupus familiaris")
    assert inv is not None
    organs = []
    for o in inv.organs:
        item = {"key": o.key, "status": "present"}
        if o.complement > 1:
            item["complement"] = "missing_some" if o.key == "leg" else "full"
        organs.append(item)
    checklist = json.dumps({"organs_present": organs, "note": "x"})
    con.execute(
        "INSERT INTO trait_rubric (id, task_id, taxon) VALUES (1, 1, ?)",
        ("Canis lupus familiaris",),
    )
    con.execute("INSERT INTO model_output (id, task_id) VALUES (10, 1)")
    con.execute(
        "INSERT INTO completeness (id, output_id, category, score, checklist_json) "
        "VALUES (100, 10, 'malformed', 1.0, ?)",
        (checklist,),
    )
    con.commit()
    con.close()


def _category(db_path: str, cid: int) -> str:
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT category FROM completeness WHERE id = ?", (cid,)).fetchone()
    con.close()
    return row[0]


def test_rederive_flips_a_malformed_row_to_complete(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    _rederive(db, apply=True)
    assert _category(db, 100) == "complete"


def test_dry_run_does_not_write(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    _rederive(db, apply=False)
    assert _category(db, 100) == "malformed"  # unchanged


def test_only_old_filter_scopes_the_rewrite(tmp_path):
    db = str(tmp_path / "t.db")
    _seed(db)
    # scoping to a category the row is NOT in leaves it untouched even with apply=True
    _rederive(db, apply=True, only_old={"partial-organism"})
    assert _category(db, 100) == "malformed"
    # scoping to its actual category rewrites it
    _rederive(db, apply=True, only_old={"malformed"})
    assert _category(db, 100) == "complete"
