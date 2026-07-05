"""Generate tomato 3D models via TEXT→3D APIs from a text prompt, and ingest them as AI outputs.
`generate_api_text` is the testable core (providers + env injected); `main()` wires the tomato
prompt, TEXT_PROVIDERS, and the recon scorer. Commits per object. API keys come from env and are
never logged.

Text→3D produces ORGAN/part-level meshes, not faithful whole plants (field-map verified) — these are the
generative-3D baseline (organ/blob-level QUALITY) the procedural path is measured against; targets the
whole plant (depiction=whole_plant, like the image-recon baseline) and is flagged `modality=text`.
Key-gated: a live run needs FAL_KEY / REPLICATE_API_TOKEN.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import ingest  # noqa: E402
from app.models import Generator, ModelOutput, Task  # noqa: E402

TOMATO_TITLE = "Solanum lycopersicum — single-image → 3D reconstruction"
TOMATO_PROMPT = (
    "A tomato plant, Solanum lycopersicum: an upright green stem with pinnately compound serrated "
    "green leaves and clusters of round red ripe tomatoes."
)

# (subject task title, whole-plant text→3D prompt) for every arena taxon. The title must match the
# existing subject Task; text→3D outputs attach to the SAME subject task as the other paradigms.
TAXA: list[tuple[str, str]] = [
    (TOMATO_TITLE, TOMATO_PROMPT),
    (
        "Zea mays — single-image → 3D reconstruction",
        "A maize plant, Zea mays: one tall upright stalk with long arching strap-shaped green "
        "leaves arranged in two ranks, a terminal feathery tassel, and lateral ears.",
    ),
    (
        "Arabidopsis thaliana — single-image → 3D reconstruction",
        "An Arabidopsis thaliana plant: a flat basal rosette of small green spoon-shaped leaves "
        "with a single thin central flowering stalk.",
    ),
    (
        "Pinus sylvestris — single-image → 3D reconstruction",
        "A Scots pine tree, Pinus sylvestris: an upright conifer with a straight trunk, whorled "
        "branches, and dense clusters of long green needles.",
    ),
    (
        "Rosa — single-image → 3D reconstruction",
        "A rose plant, Rosa: an upright thorny green shrub with pinnately compound serrated leaves "
        "and layered pink-red blooms.",
    ),
    (
        "Glycine max — single-image → 3D reconstruction",
        "A soybean plant, Glycine max: an erect branching green stem with trifoliate leaves and "
        "small hairy seed pods.",
    ),
    # Kingdom Fungi + easy-plant expansion
    (
        "Lycoperdon perlatum — single-image → 3D reconstruction",
        "A common puffball, Lycoperdon perlatum: a small round white pear-shaped fungal fruiting "
        "body covered in fine conical warts, on a short tapered base.",
    ),
    (
        "Cucurbita pepo — single-image → 3D reconstruction",
        "A pumpkin fruit, Cucurbita pepo: a single large round ribbed orange gourd with a short "
        "woody stem and a smooth convex surface.",
    ),
    (
        "Hericium erinaceus — single-image → 3D reconstruction",
        "A lion's mane mushroom, Hericium erinaceus: a rounded white fungal mass on wood covered "
        "in dense cascading icicle-like spines.",
    ),
    # Fungi expansion wave-2
    (
        "Boletus edulis — single-image → 3D reconstruction",
        "A porcini mushroom, Boletus edulis: a single large rounded red-brown convex cap on a "
        "thick swollen pale stalk, with a spongy pale pore surface underneath.",
    ),
    (
        "Amanita muscaria — single-image → 3D reconstruction",
        "A fly agaric mushroom, Amanita muscaria: a bright red convex cap covered in white wart "
        "flecks, on a white stalk with a skirt-like ring and a bulbous base.",
    ),
    (
        "Morchella esculenta — single-image → 3D reconstruction",
        "A yellow morel, Morchella esculenta: a conical cap covered in a honeycomb network of "
        "ridges and deep pits, on a pale hollow stalk.",
    ),
    (
        "Trametes versicolor — single-image → 3D reconstruction",
        "A turkey tail fungus, Trametes versicolor: thin fan-shaped bracket shelves with "
        "concentric coloured bands, growing in overlapping rosettes on wood.",
    ),
]


def _provenance(slug: str, name: str) -> tuple[str, str]:
    """(license, external_url) for an api: provider, derived from the slug prefix."""
    if slug.startswith("fal:"):
        url = "https://fal.ai"
    elif slug.startswith("replicate:"):
        url = "https://replicate.com"
    else:
        url = ""
    return f"{name} generated-asset terms (see provider)", url


def generate_api_text(
    db, prompt, *, providers, env, score_fn=None, task_title=TOMATO_TITLE
) -> dict:
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
    # Idempotency: a (task, provider) that already has a text→3D output is skipped, so an
    # interrupted batch can be re-run without duplicating meshes (generator.slug == provider slug).
    existing = set(
        db.execute(
            select(Generator.slug)
            .join(ModelOutput, ModelOutput.generator_id == Generator.id)
            .where(ModelOutput.task_id == task.id, ModelOutput.source.like("api:text:%"))
        )
        .scalars()
        .all()
    )
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

    # Providers are I/O-bound (submit→poll→download); run concurrently at no extra cost. Each fn
    # returns GLB bytes and touches no DB → the threads are safe; register+score run serially here.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _call(slug):
        fn, key, _name = to_run[slug]
        return fn(prompt, api_key=key)

    with ThreadPoolExecutor(max_workers=min(8, len(to_run))) as ex:
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
                    title=f"{name} (text→3D)",
                    meta={
                        "depiction": "whole_plant",
                        "provider": slug,
                        "modality": "text",
                        "from_prompt": True,
                    },
                )
                out.source = f"api:text:{slug}"  # api:text: → classify() routes to text_native
                out.license, out.external_url = _provenance(slug, name)
                out.attribution = f"Generated by {name} from a text prompt (text→3D, organ-level)"
                db.commit()  # provenance committed → hosted
                report["generated"] += 1
                report["by_provider"][slug] = report["by_provider"].get(slug, 0) + 1
                if score_fn is not None:
                    try:
                        score_fn(db, out)
                        db.commit()
                    except Exception as e:  # noqa: BLE001 — scoring best-effort; object stays hosted
                        print(f"  score failed for {out.id}: {e}")
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
    from app.image3d import TEXT_PROVIDERS

    ap = argparse.ArgumentParser(description="Generate whole-plant text→3D outputs per taxon.")
    ap.add_argument(
        "--crop",
        default=None,
        help="substring of a taxon title to run just one (e.g. 'Zea'); default = all 6 taxa",
    )
    ap.add_argument(
        "--no-score", action="store_true", help="skip recon scoring (AgriGen scorer down)"
    )
    args = ap.parse_args()

    active = {s: v for s, v in TEXT_PROVIDERS.items() if os.environ.get(v[1])}
    if not active:
        print(
            "no text→3D provider key in env (FAL_KEY / REPLICATE_API_TOKEN) — nothing to generate"
        )
        return 0
    taxa = TAXA
    if args.crop:
        taxa = [t for t in TAXA if args.crop.lower() in t[0].lower()]
        if not taxa:
            print(f"no taxon title matched --crop {args.crop!r}")
            return 1
    score_fn = None if args.no_score else recon_service.score_and_store
    db = SessionLocal()
    try:
        for title, prompt in taxa:
            print(f"=== {title} ===")
            report = generate_api_text(
                db, prompt, providers=active, env=os.environ, score_fn=score_fn, task_title=title
            )
            print(report)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
