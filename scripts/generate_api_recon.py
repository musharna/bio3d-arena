"""Generate tomato 3D models via image-to-3D APIs from the canonical reference photo, and
ingest them as AI-reconstruction outputs. `generate_api_recon` is the testable core
(providers + env injected); `main()` wires the real reference image, PROVIDERS, and the
recon scorer. Commits per object. API keys come from env and are never logged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import ModelOutput, Task  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"
MAIZE_TITLE = "Zea mays — single-image → 3D reconstruction"
ROSE_TITLE = "Rosa — single-image → 3D reconstruction"
ARABIDOPSIS_TITLE = "Arabidopsis thaliana — single-image → 3D reconstruction"
PINE_TITLE = "Pinus sylvestris — single-image → 3D reconstruction"
# Per-crop: subject task + its CC reference photo. The API recon path attaches api:<provider>
# outputs to the subject by title (no GT/ReconTask needed); key-gated per provider.
CROPS = {
    "tomato": {"task_title": TOMATO_TITLE, "image": "data/assets/reference/tomato_ref_clean.jpg"},
    "maize": {"task_title": MAIZE_TITLE, "image": "data/assets/reference/maize_ref.jpg"},
    "rose": {"task_title": ROSE_TITLE, "image": "data/assets/reference/rose_ref_clean.jpg"},
    "soybean": {
        "task_title": "Glycine max — single-image → 3D reconstruction",
        "image": "data/assets/reference/soybean_ref_clean.jpg",
    },
    "arabidopsis": {
        "task_title": ARABIDOPSIS_TITLE,
        "image": "data/assets/reference/arabidopsis_ref.jpg",
    },
    "pinus": {"task_title": PINE_TITLE, "image": "data/assets/reference/pinus_ref.jpg"},
    # Kingdom Fungi + easy-plant expansion (CC-clean reference photos)
    "puffball": {
        "task_title": "Lycoperdon perlatum — single-image → 3D reconstruction",
        "image": "data/assets/reference/puffball_ref.jpg",
    },
    "gourd": {
        "task_title": "Cucurbita pepo — single-image → 3D reconstruction",
        "image": "data/assets/reference/gourd_ref.jpg",
    },
    "hericium": {
        "task_title": "Hericium erinaceus — single-image → 3D reconstruction",
        "image": "data/assets/reference/hericium_ref.jpg",
    },
    # Fungi expansion wave-2 (real-specimen CC photos: bolete/amanita CC-BY-3.0, morel/turkey-tail CC0)
    "bolete": {
        "task_title": "Boletus edulis — single-image → 3D reconstruction",
        "image": "data/assets/reference/bolete_ref.jpg",
    },
    "amanita": {
        "task_title": "Amanita muscaria — single-image → 3D reconstruction",
        "image": "data/assets/reference/amanita_ref.jpg",
    },
    "morel": {
        "task_title": "Morchella esculenta — single-image → 3D reconstruction",
        "image": "data/assets/reference/morel_ref.jpg",
    },
    "turkeytail": {
        "task_title": "Trametes versicolor — single-image → 3D reconstruction",
        "image": "data/assets/reference/turkeytail_ref.jpg",
    },
}


def _provenance(slug: str, name: str) -> tuple[str, str]:
    """(license, external_url) for an api: provider, derived from the slug prefix."""
    if slug.startswith("fal:"):
        url = "https://fal.ai"
    elif slug.startswith("replicate:"):
        url = "https://replicate.com"
    elif slug == "tripo":
        url = "https://platform.tripo3d.ai"
    else:
        url = ""
    return f"{name} generated-asset terms (see provider)", url


def generate_api_recon(
    db,
    image_bytes,
    *,
    providers,
    env,
    score_fn=None,
    task_title=TOMATO_TITLE,
    input_image_rel=None,
    skip_existing=False,
    max_workers=8,
) -> dict:
    """`input_image_rel` is the storage-relative path of the photo fed to the models (e.g.
    'reference/maize_ref.jpg'); recorded per output so the page can show which image produced it.

    Providers run CONCURRENTLY (they are I/O-bound: submit→poll→download, mostly waiting on a
    remote GPU), cutting wall-time from ~sum to ~max of the provider calls at no extra cost. Each
    provider fn returns GLB bytes and touches no DB, so the threads are safe; register+score run
    serially in this thread. `skip_existing` is our own result cache — FAL/Replicate don't dedup
    identical inputs, so skipping a (task, provider) we already hold avoids re-paying on re-runs."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    task = db.execute(select(Task).where(Task.title == task_title)).scalars().first()
    if task is None:
        raise RuntimeError(f"subject task not found: {task_title!r}")
    report = {
        "generated": 0,
        "skipped_no_key": 0,
        "skipped_exists": 0,
        "errors": 0,
        "by_provider": {},
    }

    existing: set[str] = set()
    if skip_existing:
        existing = {
            o.source[len("api:") :]
            for o in db.execute(
                select(ModelOutput).where(
                    ModelOutput.task_id == task.id,
                    ModelOutput.source.like("api:%"),
                    ~ModelOutput.source.like("api:text:%"),
                )
            ).scalars()
        }

    to_run = {}  # slug -> (fn, key, name)
    for slug, (fn, env_var, name) in providers.items():
        if slug in existing:
            report["skipped_exists"] += 1
            continue
        key = env.get(env_var)
        if not key:
            report["skipped_no_key"] += 1
            continue
        to_run[slug] = (fn, key, name)
    if not to_run:
        return report

    def _call(slug):
        fn, key, _name = to_run[slug]
        return fn(image_bytes, api_key=key)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_run))) as ex:
        futures = {ex.submit(_call, slug): slug for slug in to_run}
        for fut in as_completed(futures):
            slug = futures[fut]
            name = to_run[slug][2]
            try:
                glb = fut.result()
            except Exception as e:  # noqa: BLE001 — one provider never aborts the batch
                # Provider passes the key in a header, never in exception text, so str(e) is safe.
                print(f"  {slug} generation failed: {type(e).__name__}: {e}")
                report["errors"] += 1
                continue
            try:
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
                        "input_image": input_image_rel,
                    },
                )
                out.source = f"api:{slug}"
                out.license, out.external_url = _provenance(slug, name)
                out.attribution = f"Generated by {name} from CC reference photo"
                db.commit()  # provenance committed → hosted
                report["generated"] += 1
                report["by_provider"][slug] = report["by_provider"].get(slug, 0) + 1
                if score_fn is not None:
                    try:
                        score_fn(db, out)
                        db.commit()
                    except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                        print(f"  score failed for {out.id}: {e}")
                        # Guard the rollback itself: a secondary failure here must NOT escape to
                        # the outer except (double-counting the provider as generated+error).
                        try:
                            db.rollback()
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as e:  # noqa: BLE001 — a DB/register failure for one output
                print(f"  {slug} register failed: {type(e).__name__}: {e}")
                report["errors"] += 1
                db.rollback()
    return report


def main() -> int:
    import argparse
    import os

    from app import recon_service
    from app.database import SessionLocal
    from app.image3d import PROVIDERS

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--crop", default="tomato", choices=sorted(CROPS))
    ap.add_argument(
        "--force",
        action="store_true",
        help="regenerate providers even if this task already has their output (default: skip "
        "existing — our result cache, since FAL/Replicate don't dedup identical inputs)",
    )
    args = ap.parse_args()
    crop = CROPS[args.crop]

    ref = Path(__file__).resolve().parent.parent / crop["image"]
    if not ref.exists():
        print(f"reference image missing: {ref} — source the CC photo first")
        return 1
    image_bytes = ref.read_bytes()
    # storage-relative path of the reference (asset store root is data/assets/) for per-output provenance
    input_image_rel = crop["image"].split("data/assets/", 1)[-1]
    active = {s: v for s, v in PROVIDERS.items() if os.environ.get(v[1])}
    if not active:
        print("no provider API key in env (e.g. TRIPO_API_KEY) — nothing to generate")
        return 0
    db = SessionLocal()
    try:
        report = generate_api_recon(
            db,
            image_bytes,
            providers=active,
            env=os.environ,
            score_fn=recon_service.score_and_store,
            task_title=crop["task_title"],
            input_image_rel=input_image_rel,
            skip_existing=not args.force,
        )
        print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
