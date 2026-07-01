"""Export a curated public bundle from the internal instance (SP1).

Emits out_dir/{rows.json, assets/<path>, gt/<species>.glb, manifest.json}. The single
leak chokepoint: license-gated, allowlist-only, baked-GT-only (no raw .npy), fail-loud.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.inspection import inspect as sqla_inspect  # noqa: E402

from app import config, public_export  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.storage import StorageBackend, get_storage  # noqa: E402
from app.models import (  # noqa: E402
    Category,
    Criterion,
    Generator,
    Task,
    ModelOutput,
    Comparison,
    Vote,
    Rating,
    Metric,
    GoldPair,
    ReconTask,
    TaskDifficulty,
)

# Serialization order = FK-safe insert order for import.
EXPORT_MODELS = [
    Category,
    Criterion,
    Generator,
    Task,
    ModelOutput,
    Comparison,
    Vote,
    Rating,
    Metric,
    GoldPair,
    ReconTask,
    TaskDifficulty,
]


def _row_to_dict(obj) -> dict:
    cols = sqla_inspect(obj).mapper.column_attrs
    out = {}
    for c in cols:
        v = getattr(obj, c.key)
        out[c.key] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


def _filtered_rows(db, inc: public_export.IncludeSet) -> dict[str, list[dict]]:
    all_out = inc.output_ids | inc.gold_output_ids
    tables: dict[str, list[dict]] = {}
    for model in EXPORT_MODELS:
        name = model.__tablename__
        q = select(model)
        rows = [r for r in db.execute(q).scalars()]
        keep = []
        for r in rows:
            d = _row_to_dict(r)
            if name == "task" and r.id not in inc.task_ids:
                continue
            if name == "generator" and r.id not in inc.generator_ids:
                continue
            if name == "model_output" and r.id not in all_out:
                continue
            if (
                name in ("comparison", "recon_task", "task_difficulty")
                and getattr(r, "task_id", None) not in inc.task_ids
            ):
                continue
            if name == "metric" and getattr(r, "output_id", None) not in all_out:
                continue
            if name == "rating" and getattr(r, "generator_id", None) not in inc.generator_ids:
                continue
            keep.append(d)
        tables[name] = keep
    return tables


def export_bundle(
    db, storage: StorageBackend, *, task_titles, generator_slugs, out_dir, dry_run: bool = False
) -> dict:
    inc = public_export.resolve_include_ids(
        db, task_titles=task_titles, generator_slugs=generator_slugs
    )
    public_export.check_licenses(db, inc.output_ids)  # fail-loud before writing anything
    all_out = inc.output_ids | inc.gold_output_ids
    tables = _filtered_rows(db, inc)

    licenses: dict[str, int] = {}
    for d in tables["model_output"]:
        licenses[str(d.get("license"))] = licenses.get(str(d.get("license")), 0) + 1

    manifest = {
        "version": 1,
        "counts": {k: len(v) for k, v in tables.items()},
        "licenses": licenses,
        "n_outputs": len(all_out),
    }
    if dry_run:
        manifest["dry_run"] = True
        return manifest

    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "gt").mkdir(parents=True, exist_ok=True)
    rows_bytes = json.dumps(tables, indent=0, sort_keys=True).encode()
    (out / "rows.json").write_bytes(rows_bytes)
    manifest["sha256"] = hashlib.sha256(rows_bytes).hexdigest()

    # Asset blobs for every included output.
    for d in tables["model_output"]:
        rel = d["asset_path"]
        (out / "assets" / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / "assets" / rel).write_bytes(storage.read(rel))

    # Baked GT reference GLBs only (never raw .npy). Copy whatever exists under gt/.
    for d in tables["recon_task"]:
        slug = d.get("species_slug")
        rel = f"{config.GT_ASSET_SUBDIR}/{slug}.glb"
        if slug and storage.exists(rel):
            (out / "gt" / f"{slug}.glb").write_bytes(storage.read(rel))

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, help="comma-separated task titles")
    ap.add_argument("--generators", required=True, help="comma-separated generator slugs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        m = export_bundle(
            db,
            get_storage(),
            task_titles=a.tasks.split(","),
            generator_slugs=a.generators.split(","),
            out_dir=a.out,
            dry_run=a.dry_run,
        )
    finally:
        db.close()
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
