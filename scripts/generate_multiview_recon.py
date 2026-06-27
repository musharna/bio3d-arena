"""Multi-view recon track: per subject, reference photo → NVS (N views) → existing multi-view
recon → recon:* outputs. New piece is the NVS view-generation + per-subject wiring; the MV recon
core (generate_api_multiview) + recon: source class are reused.

Run (key-gated REPLICATE_API_TOKEN + FAL_KEY):
    .venv/bin/python scripts/generate_multiview_recon.py [--subject pinus|arabidopsis] [--refresh]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.image3d import MULTIVIEW_PROVIDERS, NVS_PROVIDERS  # noqa: E402
from scripts.generate_api_multiview import generate_api_multiview  # noqa: E402

# slug → reference photo (asset-store-relative) + recon Task title (the GT-bound subject task).
SUBJECTS: dict[str, dict] = {
    "pinus": {
        "ref": "reference/pinus_ref.jpg",
        "task_title": "Pinus sylvestris — single-image → 3D reconstruction",
    },
    "arabidopsis": {
        "ref": "reference/arabidopsis_ref.jpg",
        "task_title": "Arabidopsis thaliana — single-image → 3D reconstruction",
    },
}


def run_subject(db, subject, *, env, nvs_fn, mv_providers, views_dir, score_fn=None) -> dict:
    """nvs_fn(image_bytes, api_key=...) -> list[bytes]; mv_providers like MULTIVIEW_PROVIDERS."""
    ref = Path(config.ASSET_DIR) / subject["ref"]
    if not ref.exists():
        return {"subject": subject["task_title"], "skipped": f"missing ref {ref}"}
    rep_key = env.get("REPLICATE_API_TOKEN")
    if not rep_key:
        return {"subject": subject["task_title"], "skipped": "no REPLICATE_API_TOKEN"}
    try:
        views = nvs_fn(ref.read_bytes(), api_key=rep_key)
    except Exception as e:  # noqa: BLE001
        return {"subject": subject["task_title"], "skipped": f"nvs error: {type(e).__name__}"}
    if len(views) < 2:
        return {"subject": subject["task_title"], "skipped": f"nvs returned {len(views)} views"}
    if views_dir is not None:
        views_dir = Path(views_dir)
        views_dir.mkdir(parents=True, exist_ok=True)
        for i, v in enumerate(views):
            (views_dir / f"view_{i}.png").write_bytes(v)
    active = {s: v for s, v in mv_providers.items() if env.get(v[1])}
    report = generate_api_multiview(
        db, views, providers=active, env=env, score_fn=score_fn, task_title=subject["task_title"]
    )
    return {"subject": subject["task_title"], "n_views": len(views), "recon": report}


def main() -> int:
    import argparse
    import os

    from app import recon_service
    from app.database import SessionLocal

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subject", choices=sorted(SUBJECTS), default=None)
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="ignore cached views (currently always regenerates)",
    )
    args = ap.parse_args()
    subjects = [args.subject] if args.subject else list(SUBJECTS)
    nvs_fn = NVS_PROVIDERS["zero123plusplus"][0]
    with SessionLocal() as db:
        for slug in subjects:
            vdir = Path(config.ASSET_DIR) / "reference" / "views" / slug
            res = run_subject(
                db,
                SUBJECTS[slug],
                env=os.environ,
                nvs_fn=nvs_fn,
                mv_providers=MULTIVIEW_PROVIDERS,
                views_dir=vdir,
                score_fn=recon_service.score_and_store,
            )
            print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
