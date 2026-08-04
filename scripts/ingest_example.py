#!/usr/bin/env python3
"""Example: register real generator outputs into a running Taxon3D.

This stands in for a real generator pipeline (flower-sim, Blender/GeoNodes rose,
plant world model, …). The "bake" step here uses the procedural asset generator;
in production you'd replace `bake_glb(...)` with your generator producing a GLB
from a gene / parameter vector / prompt.

Usage:
    # start the server first:  uvicorn app.main:app
    BIO3D_ADMIN_TOKEN=changeme-admin-token python scripts/ingest_example.py
    python scripts/ingest_example.py --base-url http://localhost:8000 --token ...
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# Make the project importable when run directly as `python scripts/ingest_example.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets_gen import build_asset  # noqa: E402  stand-in for a real generator
from app.client import Taxon3DClient  # noqa: E402


def bake_glb(shape: str, seed: int, out_dir: Path) -> Path:
    """Produce a GLB for (shape, seed). Replace with your real generator."""
    path = out_dir / f"{shape}_{seed}.glb"
    build_asset(shape, seed, path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("BIO3D_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("BIO3D_ADMIN_TOKEN", "changeme-admin-token"))
    args = ap.parse_args()

    c = Taxon3DClient(args.base_url, admin_token=args.token)
    print("health:", c.health())

    # 1) Ensure the taxonomy + task exist.
    c.upsert_category("flowers", "Flowers")
    task = c.create_task(
        "flowers",
        "Ingested rose bloom",
        "Generate an open rose flower with layered petals.",
    )
    print("task:", task)

    # 2) Register two generators, each producing a distinct baked GLB.
    tmp = Path(tempfile.mkdtemp(prefix="bio3d_ingest_"))
    for gen_slug, gen_name, seed in [
        ("rose-gen-v1", "Rose Generator v1", 101),
        ("rose-gen-v2", "Rose Generator v2", 202),
    ]:
        c.upsert_generator(gen_slug, gen_name)
        glb = bake_glb("flower", seed, tmp)
        result = c.register_output(
            task["id"],
            gen_slug,
            glb,
            title=f"{task['title']} — {gen_name}",
            meta={"seed": seed, "pipeline": "ingest_example", "version": gen_name},
        )
        print(
            f"registered {gen_slug}: id={result['id']} created={result['created']} "
            f"vtx={result['meta'].get('vertices')}"
        )

    # 3) Idempotency demo: re-registering the same bytes does not duplicate.
    glb = bake_glb("flower", 101, tmp)
    dup = c.register_output(task["id"], "rose-gen-v1", glb)
    print("re-register same asset -> created:", dup["created"], "(expected False)")

    # 4) Recompute and show the (still-empty) leaderboard scaffold.
    print("recompute:", c.recompute().get("status"))
    print("leaderboard rows:", len(c.leaderboard()))
    print("\nDone. Vote on the new pairing at", args.base_url)
    c.close()


if __name__ == "__main__":
    main()
