# app/gt_render.py
"""Bake held-out GT scan clouds into renderable reference GLBs.

The scorer's GT bundle (config.GT_BUNDLE_DIR) holds per-species (N,3) .npy point clouds
in centimetres, +Z-up. `bake_species_gt` converts one representative scan per species to a
+Y-up POINTS GLB in bio3d's own asset store (run once at build time by scripts/render_gt.py).
`find_gt_glb` is the runtime lookup the Reference panel uses — it only touches bio3d storage,
so the running server never depends on the scorer's filesystem.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .points_convert import npy_to_glb
from .storage import StorageBackend, get_storage


def gt_glb_rel_path(species_slug: str) -> str:
    """Storage key for a species' baked GT reference GLB (stable, version-independent)."""
    return f"{config.GT_ASSET_SUBDIR}/{species_slug}.glb"


def representative_gt_npy(species_slug: str, bundle_dir: Path | None = None) -> Path | None:
    """The first GT .npy for a species (sorted), or None if the bundle has none."""
    root = Path(bundle_dir) if bundle_dir is not None else config.GT_BUNDLE_DIR
    species_dir = root / species_slug
    if not species_dir.is_dir():
        return None
    npys = sorted(species_dir.glob("*.npy"))
    return npys[0] if npys else None


def bake_species_gt(
    species_slug: str,
    *,
    bundle_dir: Path | None = None,
    storage: StorageBackend | None = None,
    max_points: int = 200_000,
    seed: int = 0,
) -> str | None:
    """Convert one representative GT scan → +Y-up POINTS GLB, save to storage.

    Returns the storage rel path, or None if the bundle has no scan for this species.
    """
    npy = representative_gt_npy(species_slug, bundle_dir)
    if npy is None:
        return None
    glb = npy_to_glb(str(npy), up_axis="z", max_points=max_points, seed=seed)
    store = storage if storage is not None else get_storage()
    rel = gt_glb_rel_path(species_slug)
    store.save(rel, glb)
    return rel


def find_gt_glb(species_slug: str, storage: StorageBackend | None = None) -> str | None:
    """Runtime: the storage rel path of a baked GT GLB if present, else None."""
    store = storage if storage is not None else get_storage()
    rel = gt_glb_rel_path(species_slug)
    return rel if store.exists(rel) else None
