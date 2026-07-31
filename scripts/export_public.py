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

from app import config, mesh_compress, public_export, texture_downscale  # noqa: E402
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
    KingdomRating,
    JudgeRating,
    KingdomJudgeRating,
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
    # The other three cached boards. `Rating` alone used to ship, which quietly meant the public
    # instance could show the global human board and nothing else: the kingdom boards had no cache
    # to read, and the AI-judge board rendered its empty state ("No automated LLM-judge rankings
    # for this selection yet") on a project that has judged four paradigms.
    #
    # The judge boards are the ones that MUST be promoted rather than recomputed. A cached rating
    # is normally reproducible from the votes beside it, but `judge_vote` is not exported — a judge
    # ballot references outputs the posture filter may drop, so shipping the ballots would dangle.
    # Without the fitted ratings there is therefore no path to a populated judge board at all, and
    # the page is blank forever. This is exactly the runbook's "promote, don't recompute".
    KingdomRating,
    JudgeRating,
    KingdomJudgeRating,
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
            # Every cached board is keyed by generator, and a generator the posture dropped is not
            # in the bundle — so its rating row would dangle on import and, worse, would rank an
            # entrant the public site does not show.
            if (
                name in ("rating", "kingdom_rating", "judge_rating", "kingdom_judge_rating")
                and getattr(r, "generator_id", None) not in inc.generator_ids
            ):
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


class ReferenceLicenseError(RuntimeError):
    """A gallery photo cannot be redistributed under its license."""


def copy_reference_gallery(out: Path, posture: str) -> int:
    """Ship the CC species galleries into the bundle. Returns the photo count.

    They go under `assets/reference/gallery/<slug>/`, which is not decorative placement: the
    uploader (`import_public._bundle_assets`) keys everything under `assets/` relative to it, so
    a photo lands in storage at exactly `reference/gallery/<slug>/<file>` — the key
    `service.reference_images_for_task` asks for. Any other layout uploads real files under a
    path nothing reads, which looks like a successful publish and still shows no images.

    Before this the galleries shipped nowhere at all: the export wrote only output blobs and
    gt/, and the image excludes `data/`, so the public instance had no galleries in either place
    and every task rendered `references: []`.

    QA-failed photos are withheld AND pruned from the shipped manifest, so a manifest entry can
    never point at a file that was not shipped (which would render a broken image).

    Licensing follows the posture split already used for recon input photos: shipping the photo
    FILES for redistribution requires a redistributable license, and that gate is fail-loud.
    `display` does not redistribute — the photos are shown with their required attribution, the
    same posture under which the meshes are displayed — so it does not gate.
    """
    from app.licensing import REDISTRIBUTABLE_LICENSES, normalize_license

    src = config.ASSET_DIR / "reference" / "gallery"
    if not src.is_dir():
        return 0
    n = 0
    for manifest_path in sorted(src.glob("*/manifest.json")):
        slug = manifest_path.parent.name
        try:
            items = json.loads(manifest_path.read_text())
        except ValueError as e:
            raise ReferenceLicenseError(f"unreadable gallery manifest for {slug}: {e}") from e
        keep = [i for i in items if isinstance(i, dict) and i.get("passed_qa", True)]

        if posture == "redistribute":
            bad = [
                (slug, i.get("file"), i.get("license"))
                for i in keep
                if normalize_license(i.get("license")) not in REDISTRIBUTABLE_LICENSES
            ]
            if bad:
                raise ReferenceLicenseError(
                    f"{len(bad)} reference photo(s) are not redistributable under their license "
                    f"— refusing to ship them in a redistribute bundle: {bad[:5]}"
                )

        dst = out / "assets" / "reference" / "gallery" / slug
        dst.mkdir(parents=True, exist_ok=True)
        shipped = []
        for i in keep:
            f = manifest_path.parent / str(i.get("file", ""))
            if not i.get("file") or not f.is_file():
                continue
            (dst / i["file"]).write_bytes(f.read_bytes())
            shipped.append(i)
            n += 1
        (dst / "manifest.json").write_text(json.dumps(shipped, indent=1))
    return n


def export_bundle(
    db,
    storage: StorageBackend,
    *,
    task_titles,
    generator_slugs,
    out_dir,
    posture: str = "redistribute",
    dry_run: bool = False,
    compress: bool = False,
) -> dict:
    """`compress` defaults OFF here and ON at the command line (see main).

    That asymmetry is deliberate. Compression needs a Node toolchain, and defaulting it on in the
    library made every programmatic caller — the export tests among them — fail with
    ToolchainUnavailable on a machine whose only Node was 18. A release goes out through
    `python -m scripts.export_public`, which opts in and fails loudly if the toolchain is missing;
    a caller that never asked to compress should not inherit a Node dependency. The manifest
    records `compression.enabled`, so an uncompressed bundle is never silent.
    """
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
        # Input-photo (derivative) clearance is a REDISTRIBUTION gate: a redistributed recon mesh
        # is a derivative of its input photo, so that photo must be CC-cleared. Displaying the
        # mesh (no download, AI-labeled) does not redistribute the photo, so display is exempt —
        # the un-cleared input photo is instead suppressed from the vote UI
        # (service.reference_images_for_task). "Show the mesh, never the photo."
        assert_recon_photos_cleared(db, inc.output_ids)
        assert_recon_photos_cleared_for_gold(db, inc.gold_output_ids)
    # else display: no input-photo gate.
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

    # Asset blobs for every included output, Draco-compressed on the way into the bundle.
    manifest["compression"] = _stage_assets(out, storage, tables["model_output"], compress=compress)

    manifest["reference_photos"] = copy_reference_gallery(out, posture)

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


def _stage_assets(out: Path, storage: StorageBackend, rows: list[dict], *, compress: bool) -> dict:
    """Write every output blob into the bundle, Draco-compressing GLBs as they land.

    Compression belongs HERE rather than in the corpus or the web app:

    * the internal corpus stays byte-identical, so reproducibility, any dataset release, and the
      votes already cast against those exact meshes are untouched;
    * `asset_path` is unchanged, so nothing downstream — import, the DB, `media_asset` — needs to
      know this happened. The bundle simply carries a smaller file at the same key. That is the
      same "promote, don't recompute" shape the scores and leaderboards already use;
    * the public host never needs the Node toolchain, which matters because `requirements.txt` is
      deliberately Python-only (PR #87: 173 MB runtime vs 5.6 GB with the research stack).

    Measured motivation: 68% of the 5.35 GB votable roster is raw geometry, and a PAIRWISE ballot
    already takes 83 s to become comparable on Fast 4G.
    """
    stats = {
        "compressed": 0,
        "skipped_not_glb": 0,
        "kept_original": 0,
        "textures_resized": 0,
        "enabled": compress,
    }
    node = cli = None
    if compress:
        node = mesh_compress.node_binary()
        mesh_compress.require_node(node)  # fail loud and early, not per-file
        cli = mesh_compress.cli_entry()
        if not cli or not Path(cli).exists():
            raise mesh_compress.ToolchainUnavailable(
                "BIO3D_GLTF_TRANSFORM_CLI is unset or does not exist. Install "
                "@gltf-transform/cli and point it there, or re-run with --no-compress to ship "
                "uncompressed meshes (a pairwise ballot then costs ~83 s on 4G)."
            )

    before_total = after_total = 0
    for d in rows:
        rel = d["asset_path"]
        dst = out / "assets" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        raw = storage.read(rel)
        dst.write_bytes(raw)
        before_total += len(raw)
        if not compress or not mesh_compress.is_candidate(rel):
            stats["skipped_not_glb"] += (
                0 if not compress else int(not mesh_compress.is_candidate(rel))
            )
            after_total += len(raw)
            continue
        # Stage 2 BEFORE stage 1: shrink textures while the container is still plain glTF.
        # Draco rewrites the buffer wholesale, so running it first would mean decoding and
        # re-encoding the geometry just to reach the images.
        #
        # A file that carries a .glb extension but is not a GLB is a corpus problem, not something
        # to route around: it would reach voters as a mesh that never loads. Fail, but name the
        # asset — an anonymous ValueError partway through a 939-file export tells the operator
        # nothing about which file to go and look at.
        try:
            shrunk, tex_stats = texture_downscale.downscale_glb(dst.read_bytes())
        except ValueError as e:
            raise ValueError(f"{rel}: not a readable GLB ({e})") from e
        if tex_stats["resized"]:
            dst.write_bytes(shrunk)
            stats["textures_resized"] += tex_stats["resized"]

        tmp = dst.with_suffix(dst.suffix + ".draco")
        res = mesh_compress.compress_glb(dst, tmp, node=node, cli_entry=cli)
        if res.kept:
            tmp.replace(dst)
            stats["compressed"] += 1
        else:
            # Draco enlarged it; the original is already in place and is what we ship.
            stats["kept_original"] += 1
        after_total += dst.stat().st_size

    stats["bytes_before"] = before_total
    stats["bytes_after"] = after_total
    stats["ratio"] = round(before_total / after_total, 3) if after_total else 0.0
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", required=True, help="comma-separated task titles")
    ap.add_argument("--generators", required=True, help="comma-separated generator slugs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--posture", default="redistribute", choices=["display", "redistribute"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-compress",
        action="store_true",
        help="ship meshes uncompressed (a pairwise ballot then costs ~83s on Fast 4G)",
    )
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
            compress=not a.no_compress,
        )
        # Advisory (non-blocking, NOT a gate): flag exported taxa whose recon completeness sits
        # far below text→3D — a suspect reference/capture the operator should inspect before
        # publishing (this is the signal that caught the Cucurbita reference bug).
        from app.completeness import recon_reliability_flags

        exported_titles = a.tasks.split(",")
        flagged = [
            f["taxon"]
            for f in recon_reliability_flags(db)
            if f["flag"] and any(f["taxon"] in title for title in exported_titles)
        ]
        if flagged:
            print(
                f"⚠ recon-reliability advisory: {len(flagged)} exported taxon(s) have recon "
                f"completeness far below text→3D (suspect reference/capture) — inspect before "
                f"publishing: {', '.join(flagged)}",
                file=sys.stderr,
            )
    finally:
        db.close()
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
