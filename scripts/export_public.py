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
    # Pre-pass: a comparison ships only if its task AND BOTH referenced outputs are in the
    # bundle -- otherwise output_a_id/output_b_id would dangle (partial-generator promotion).
    included_comparison_ids = {
        c.id
        for c in db.execute(select(Comparison)).scalars()
        if c.task_id in inc.task_ids and c.output_a_id in all_out and c.output_b_id in all_out
    }
    # Referential completeness: gold decoy outputs belong to the "calibration" generator,
    # which a curator's generator_slugs allowlist won't include (resolve_include_ids adds
    # gold outputs to gold_output_ids via the GoldPair loop, not generator_ids). Every
    # generator actually referenced by an included output must ship too, or the output's
    # generator_id dangles on import.
    gen_keep = set(inc.generator_ids) | {
        o.generator_id for o in db.execute(select(ModelOutput)).scalars() if o.id in all_out
    }
    tables: dict[str, list[dict]] = {}
    for model in EXPORT_MODELS:
        name = model.__tablename__
        keep = []
        for r in db.execute(select(model)).scalars():
            if name == "task" and r.id not in inc.task_ids:
                continue
            if name == "generator" and r.id not in gen_keep:
                continue
            if name == "model_output" and r.id not in all_out:
                continue
            if name == "comparison" and r.id not in included_comparison_ids:
                continue
            if name == "vote" and r.comparison_id not in included_comparison_ids:
                continue
            if (
                name in ("recon_task", "task_difficulty")
                and getattr(r, "task_id", None) not in inc.task_ids
            ):
                continue
            if name == "metric" and getattr(r, "output_id", None) not in all_out:
                continue
            if name == "rating" and getattr(r, "generator_id", None) not in inc.generator_ids:
                continue
            if name == "gold_pair" and (
                r.task_id not in inc.task_ids
                or r.good_output_id not in all_out
                or r.bad_output_id not in all_out
            ):
                continue
            d = _row_to_dict(r)
            if name == "task" and d.get("reference_asset_id") not in all_out:
                d["reference_asset_id"] = None
            keep.append(d)
        tables[name] = keep
    return tables


def export_bundle(
    db,
    storage: StorageBackend,
    *,
    task_titles,
    generator_slugs,
    out_dir,
    posture: str = "redistribute",
    dry_run: bool = False,
) -> dict:
    from app import admissibility
    from app.reference_provenance import (
        assert_recon_photos_cleared,
        assert_recon_photos_cleared_for_gold,
    )

    inc = public_export.resolve_include_ids(
        db, task_titles=task_titles, generator_slugs=generator_slugs
    )
    gated = admissibility.non_admitted_output_ids(db)  # structural ∪ completeness ∪ semantic(gate)
    public_export.filter_include_for_posture(db, inc, posture, gated)
    public_export.filter_gold_for_posture(db, inc, posture, gated)
    if posture == "redistribute":
        public_export.check_licenses(db, inc.output_ids)  # fail-loud: nothing non-CC ships
    else:  # display
        assert_recon_photos_cleared(db, inc.output_ids)  # fail-loud: no uncleared reference photo
        # Gold rows alias a real (possibly recon) asset under decoy source/meta_json -- the
        # gate above reads each output's OWN source/meta_json and so silently skips every gold
        # id, whose true reference photo (per the twin it aliases) was never clearance-checked.
        assert_recon_photos_cleared_for_gold(db, inc.gold_output_ids)
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
        "posture": posture,
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
    # INVARIANT: gt/ reference GLBs are bio3d's own held-out meshes / CC-licensed scans -- not
    # license-gated here; must hold before any redistribute publish (no license/posture check
    # runs on this loop).
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
    ap.add_argument("--posture", default="redistribute", choices=["display", "redistribute"])
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
            posture=a.posture,
            dry_run=a.dry_run,
        )
    finally:
        db.close()
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
