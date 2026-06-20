"""DB wiring for the structure-validation track: compute metrics from stored asset
bytes and cache them in model_output.meta_json["validation"]. Best-effort — a bad
asset stores an error stub and never aborts the batch.

Aggregations (validity_leaderboard, reference_accuracy) feed the /validation page.
Kept separate from service.py (which owns the vote/Elo/BT path) — validation is an
objective, computed track distinct from the human aesthetic vote.
"""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import validation
from .models import Generator, ModelOutput, Task
from .storage import get_storage


def _read_text(output: ModelOutput) -> str:
    return get_storage().read(output.asset_path).decode("utf-8", errors="replace")


def validate_and_store(db: Session, output: ModelOutput) -> dict:
    """Compute self (+ reference if the task has a different reference) and merge into meta."""
    try:
        text = _read_text(output)
        self_res = validation.validate_output(text, output.asset_format)
    except Exception as e:  # noqa: BLE001 — best-effort; capture and continue
        self_res = {"status": "error", "reason": str(e)}
    result = {"self": self_res}

    task = output.task
    ref_id = getattr(task, "reference_asset_id", None)
    if ref_id and ref_id != output.id and output.asset_format in validation.MOLECULAR:
        ref = db.get(ModelOutput, ref_id)
        if ref is not None and ref.asset_format == output.asset_format:
            try:
                result["reference"] = validation.compare_to_reference(
                    _read_text(output), _read_text(ref), output.asset_format
                )
            except Exception as e:  # noqa: BLE001
                result["reference"] = {"status": "error", "reason": str(e)}

    meta = json.loads(output.meta_json or "{}")
    meta["validation"] = result
    output.meta_json = json.dumps(meta)
    db.flush()
    return result


def revalidate_all(db: Session) -> dict:
    outputs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    molecular = errors = 0
    for o in outputs:
        res = validate_and_store(db, o)
        status = res["self"].get("status")
        if status == "ok":
            molecular += 1
        elif status == "error":
            errors += 1
    db.commit()
    return {"outputs": len(outputs), "molecular": molecular, "errors": errors}


def _gen_name(db: Session, gid: int) -> str:
    g = db.get(Generator, gid)
    return g.name if g else str(gid)


def validity_leaderboard(db: Session) -> list[dict]:
    """Per-generator objective physical-validity aggregate over molecular outputs."""
    acc = defaultdict(lambda: {"scores": [], "clashes": [], "clean": 0, "minor": 0, "major": 0})
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    for o in outs:
        v = json.loads(o.meta_json or "{}").get("validation", {}).get("self", {})
        if v.get("status") != "ok":
            continue
        a = acc[o.generator_id]
        a["scores"].append(v["validity_score"])
        a["clashes"].append(v["clashes_per_1000"])
        a[v["tier"]] += 1
    rows = []
    for gid, a in acc.items():
        n = len(a["scores"])
        if n == 0:
            continue
        rows.append(
            {
                "generator": _gen_name(db, gid),
                "n_molecular": n,
                "mean_validity": round(sum(a["scores"]) / n, 1),
                "mean_clashes": round(sum(a["clashes"]) / n, 2),
                "clean": a["clean"],
                "minor": a["minor"],
                "major": a["major"],
            }
        )
    rows.sort(key=lambda r: r["mean_validity"], reverse=True)
    return rows


def reference_accuracy(db: Session) -> list[dict]:
    """Per task with a reference: each output's RMSD + TM-score vs the reference."""
    tasks = db.execute(select(Task).where(Task.reference_asset_id.isnot(None))).scalars().all()
    out = []
    for t in tasks:
        ref = db.get(ModelOutput, t.reference_asset_id)
        ref_gen = _gen_name(db, ref.generator_id) if ref else "—"
        rows = []
        for o in db.execute(select(ModelOutput).where(ModelOutput.task_id == t.id)).scalars().all():
            r = json.loads(o.meta_json or "{}").get("validation", {}).get("reference")
            if r and r.get("status") == "ok":
                rows.append(
                    {
                        "generator": _gen_name(db, o.generator_id),
                        "rmsd": r["rmsd"],
                        "tm_score": r["tm_score"],
                    }
                )
        if rows:
            rows.sort(key=lambda x: x["tm_score"], reverse=True)
            out.append({"task": t.title, "reference_generator": ref_gen, "rows": rows})
    return out
