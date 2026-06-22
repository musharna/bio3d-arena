"""Multi-view reconstruction: feed N views of the tomato to a feed-forward multi-view→mesh API and
ingest the result. `generate_api_multiview` is the testable core (providers + env injected); `main()`
sources the view images (the "compose synthetic views → 3D" input — rendered or novel-view-synthesized
views) and the recon scorer. Commits per object. API keys come from env and are never logged.

This is the multi-view modality (distinct from single-image→3D): COLMAP fails to pose AI-generated
views, so we skip SfM and feed views directly to a feed-forward model. Key-gated (FAL_KEY) AND
view-gated (needs ≥2 view images).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Task  # noqa: E402

# Reuses the recon subject Task (its GT bundle) so the recon scorer binds; the modality differs (N views).
TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"


def _provenance(name: str) -> tuple[str, str]:
    return f"{name} generated-asset terms (see provider)", "https://fal.ai"


def generate_api_multiview(
    db, views, *, providers, env, score_fn=None, task_title=TOMATO_TITLE
) -> dict:
    """views: list[bytes] of the input view images. Hosts each provider's recon as source='recon:*'."""
    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {"generated": 0, "skipped_no_key": 0, "errors": 0, "by_provider": {}}
    for slug, (fn, env_var, name) in providers.items():
        key = env.get(env_var)
        if not key:
            report["skipped_no_key"] += 1
            continue
        try:
            glb = fn(views, api_key=key)
            out, _created = ingest.register_output(
                db,
                task_id=task.id,
                generator_slug=slug,
                generator_name=name,
                data=glb,
                ext="glb",
                title=f"{name} recon",
                meta={
                    "depiction": "whole_plant",
                    "provider": slug,
                    "modality": "multiview",
                    "n_views": len(views),
                },
            )
            out.source = f"{slug}"  # slug already carries the recon: prefix
            out.license, out.external_url = _provenance(name)
            out.attribution = (
                f"Multi-view reconstruction by {name} from {len(views)} synthetic views"
            )
            db.commit()  # provenance committed → hosted
            report["generated"] += 1
            report["by_provider"][slug] = report["by_provider"].get(slug, 0) + 1
            if score_fn is not None:
                try:
                    score_fn(db, out)
                    db.commit()
                except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                    print(f"  score failed for {out.id}: {e}")
                    # Guard the rollback: a secondary failure must NOT escape to the outer except
                    # (it would double-count this provider as generated+error).
                    try:
                        db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as e:  # noqa: BLE001 — one provider never aborts the batch
            # Provider passes the key in a header, never in exception text, so str(e) is safe.
            print(f"  {slug} reconstruction failed: {type(e).__name__}: {e}")
            report["errors"] += 1
            db.rollback()
    return report


def main() -> int:
    import os

    from app import recon_service
    from app.database import SessionLocal
    from app.image3d import MULTIVIEW_PROVIDERS

    # Views are the "compose synthetic views" input: a dir of view images (rendered or NVS-generated
    # from the reference). Default location; override with VIEWS_DIR.
    views_dir = Path(
        os.environ.get(
            "VIEWS_DIR",
            str(Path(__file__).resolve().parent.parent / "data/assets/reference/views"),
        )
    )
    view_files = (
        sorted(p for p in views_dir.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if views_dir.exists()
        else []
    )
    if len(view_files) < 2:
        print(f"need ≥2 view images in {views_dir} (set VIEWS_DIR) — nothing to reconstruct")
        return 0
    views = [p.read_bytes() for p in view_files]

    active = {s: v for s, v in MULTIVIEW_PROVIDERS.items() if os.environ.get(v[1])}
    if not active:
        print("no multi-view provider key in env (FAL_KEY) — nothing to generate")
        return 0
    db = SessionLocal()
    try:
        report = generate_api_multiview(
            db,
            views,
            providers=active,
            env=os.environ,
            score_fn=recon_service.score_and_store,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
