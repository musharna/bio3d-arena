"""Build a citable dataset release: SP1 export bundle + LICENSE + DATASHEET + VERSION + votes."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, dataset  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.storage import StorageBackend, get_storage  # noqa: E402
from scripts.export_public import export_bundle  # noqa: E402

_TEXT_SUFFIXES = {".json", ".md", ".txt", ""}
_BINARY_PATH_PARTS = {"assets", "gt"}


def _assert_no_leak(root: Path) -> None:
    if list(root.rglob("*.npy")):
        raise RuntimeError(f"release {root} contains raw .npy GT — refusing to publish")
    needles = ("/home/mjarnold/agrigen", str(config.GT_BUNDLE_DIR))
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = set(p.relative_to(root).parts[:-1])
        if rel_parts & _BINARY_PATH_PARTS:
            continue  # baked GT / asset blobs (GLB/PLY) — binary, never text-leak carriers
        if p.suffix not in _TEXT_SUFFIXES:
            continue
        text = p.read_bytes().decode("utf-8", "ignore")
        if any(needle in text for needle in needles):
            raise RuntimeError(f"release {root} leaks an agrigen path in {p}")


def build_release(
    db, storage: StorageBackend, *, version, task_titles, generator_slugs, out_dir
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = export_bundle(
        db,
        storage,
        task_titles=task_titles,
        generator_slugs=generator_slugs,
        out_dir=out / "bundle",
    )
    rows = json.loads((out / "bundle" / "rows.json").read_text())
    rollup = dataset.license_rollup(rows.get("model_output", []))

    (out / "LICENSE").write_text(dataset.render_license(rollup))
    (out / "DATASHEET.md").write_text(dataset.render_datasheet(version, manifest, rollup))
    (out / "VERSION").write_text(f"{version}\nsha256:{manifest.get('sha256', '')}\n")
    comp_ids = {r["id"] for r in rows.get("comparison", [])}
    (out / "preference_records.json").write_text(
        json.dumps(dataset.build_preference_records(db, comparison_ids=comp_ids))
    )

    _assert_no_leak(out)

    tarball = out.parent / f"{version}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(out, arcname=version)
    return {
        "version": version,
        "sha256": manifest.get("sha256", ""),
        "n_outputs": manifest.get("n_outputs", 0),
        "tarball": str(tarball),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--tasks", required=True, help="comma-separated task titles")
    ap.add_argument("--generators", required=True, help="comma-separated generator slugs")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    db = SessionLocal()
    try:
        summary = build_release(
            db,
            get_storage(),
            version=a.version,
            task_titles=a.tasks.split(","),
            generator_slugs=a.generators.split(","),
            out_dir=a.out,
        )
    finally:
        db.close()
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
