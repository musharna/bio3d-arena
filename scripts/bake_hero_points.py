"""Bake the home-hero point clouds, one per kingdom, into app/static/hero/<kingdom>.json.

- plants : the held-out GT *scan* (a POINTS glb) — real ground truth, downsampled.
- fungi / animals : surface-sampled + outlier-cleaned from the top real *reconstruction*
  mesh for that kingdom (no GT scan exists off-plants).

Promote-don't-recompute: run this once in the internal instance (where the GT bundle +
reconstructions live), commit the JSON, and the public deploy serves it statically. Re-run
only when the source assets change.

Usage:
    BIO3D_DATA_DIR=/path/to/data python scripts/bake_hero_points.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config, hero_points, kingdoms  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ModelOutput, Task  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "hero"

# Plants use a real GT scan; maize is the flagship and lightest bundle.
PLANT_GT = "gt/zea_mays.glb"

# Curated hero specimens for the mesh kingdoms. The most-compared reconstruction isn't always
# the most legible or well-formed one (e.g. the top animal recon is a partly-malformed monarch),
# so we pin clean, recognizable specimens that were vetted visually. Falls back to
# _top_reconstruction_glb() if a pinned asset isn't present in this data dir.
CURATED_RECON = {
    "fungi": "uploads/051288ec3ef84bc987ff730ebb22dae6.glb",  # Amanita muscaria (fly agaric)
    "animals": "uploads/d6ad21678b1c4a2e9d6bca59ec63f4df.glb",  # Canis lupus familiaris (dog)
}

# Kingdoms whose curated recon is a multi-specimen scene — keep only the largest connected
# component so the hero shows one specimen. The fly-agaric recon is a cluster of three
# mushrooms; isolating leaves the central mature cap-and-stem one.
ISOLATE_MAIN = {"fungi"}


def _top_reconstruction_glb(db, kingdom: str) -> tuple[str, int] | None:
    """The most-compared, on-disk, triangle-mesh reconstruction for a kingdom."""
    cat_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    if not cat_ids:
        return None
    task_ids = set(db.execute(select(Task.id).where(Task.category_id.in_(cat_ids))).scalars())
    q = (
        select(ModelOutput)
        .where(
            ModelOutput.task_id.in_(task_ids),
            ModelOutput.asset_format == "glb",
            ModelOutput.hidden_at.is_(None),
        )
        .order_by(ModelOutput.n_comparisons.desc(), ModelOutput.id.asc())
    )
    for out in db.execute(q).scalars():
        path = Path(str(config.ASSET_DIR)) / out.asset_path
        if not path.exists():
            continue
        try:
            positions, triangles = hero_points.mesh_arrays(path.read_bytes())
        except Exception:
            continue  # points-mode or unparseable — skip, want a mesh to sample
        if len(positions) >= 200 and len(triangles) >= 100:
            return out.asset_path, len(positions)
    return None


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    root = Path(str(config.ASSET_DIR))

    # plants: GT scan
    gt = root / PLANT_GT
    if not gt.exists():
        print(f"FATAL: plant GT scan missing: {gt}", file=sys.stderr)
        return 1
    cloud = hero_points.prepare_cloud(hero_points.points_arrays(gt.read_bytes()), None)
    (OUT_DIR / "plants.json").write_text(json.dumps(cloud, separators=(",", ":")))
    print(f"plants  <- {PLANT_GT} (GT scan) -> {len(cloud)} pts")

    # fungi / animals: curated (or top) reconstruction mesh, surface-sampled + cleaned
    with SessionLocal() as db:
        for kingdom in ("fungi", "animals"):
            pinned = CURATED_RECON.get(kingdom)
            if pinned and (root / pinned).exists():
                rel = pinned
            else:
                pick = _top_reconstruction_glb(db, kingdom)
                if pick is None:
                    print(f"FATAL: no reconstruction mesh found for {kingdom}", file=sys.stderr)
                    return 1
                rel = pick[0]
            positions, triangles = hero_points.mesh_arrays((root / rel).read_bytes())
            nverts = len(positions)
            iso = kingdom in ISOLATE_MAIN
            # sample more when isolating, so the kept component still yields a full 5k cloud;
            # a tighter eps (3x) also severs thin artifact tails weakly bridged to the body.
            cloud = hero_points.prepare_cloud(
                positions,
                triangles,
                n=14000 if iso else 6000,
                isolate_main=iso,
                isolate_eps_mult=3.0 if iso else 6.0,
                # crop the sparse stem base/foot that reads as a stray tail (fungi only)
                crop_base_frac=0.15 if iso else 0.0,
            )
            (OUT_DIR / f"{kingdom}.json").write_text(json.dumps(cloud, separators=(",", ":")))
            print(f"{kingdom:7s} <- {rel} (recon, {nverts} verts) -> {len(cloud)} pts")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
