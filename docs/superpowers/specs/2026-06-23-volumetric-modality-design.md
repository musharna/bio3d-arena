# Volumetric (CT/MRI) modality — design

**Date:** 2026-06-23
**Status:** approved (brainstorm) → writing-plans next
**Tier:** maize-coverage plan Tier 3 (the "novel modality" pilot)
**Builds on:** `app/source_scans.py`, `app/points_convert.py`, `app/mesh_convert.py`, `app/sourcing.py`, `app/spotlight.py`

## Motivation

The arena scores 3D plant models across source classes — `ai` (photo→3D recon / neural frontier),
`procedural`, `scan` (real measured 3D: LiDAR/photogrammetry point clouds + meshes), and `found`
(artist assets). It has **no volumetric / tomographic sensor modality**: CT, micro-CT, X-ray, or MRI.
Volumetric imaging is a distinct, real way to obtain ground-truth 3D structure (a different sensor than
the laser/photogrammetry scans already in the `scan` class), and it is an entire phenotyping subfield
for crops. This adds that sensor axis to the arena: a real volume → marching-cubes surface mesh → GLB.

## The maize-volumetric gap (verified 2026-06-23 — record this, it gates the data choice)

Tier 3 originally targeted **maize** volumetric data. An exhaustive sweep (MorphoSource JSON API,
iDigBio, EMPIAR, EBI BioStudies/BioImage Archive, OSF, Harvard Dataverse, Mendeley Data, CyVerse,
IPK e!DAL, TomoBank, Zenodo/Figshare/Dryad APIs, Google Dataset Search, plus ~40 papers' data-
availability statements via OpenAlex/Europe PMC) established:

- **No open maize-anatomy volume exists.** Maize kernel/ear CT is a large subfield but the data is
  uniformly "available upon request" (closed). The real maize CT volumes that have _files_ are
  license-blocked: TopoRoot+ maize root CT = unmarked-license on WashU Box (the repo's own policy in
  `sourcing.classify_license` excludes unmarked → ARR); Rootine v2 maize root XCT = CC-BY-NC-ND **and**
  request-gated; Southampton synchrotron maize-root-in-soil = license unverified/unmarked.
- **MorphoSource has zero maize** (verified via its JSON API: `Zea mays`=0, `Zea`=0; the 5 "corn" hits
  are one Inca terracotta artifact).
- The **only** verified-open maize volume is a maize _tamale_ (cooked food) micro-CT — off-subject for a
  plant page (the same reason the Tier-2 Objaverse corn ears were trimmed).

Decision (user-approved): **maize volumetric is logged as a genuine coverage gap**; build the modality
now on the cleanest open **cereal stand-in** (same Poaceae family), with the plumbing ready to ingest
maize the moment open maize data appears.

## Data + subject (approved)

- **Dataset:** **barley root MRI** — IPK e!DAL-PGP, DOI `10.5447/IPK/2017/10`, _"3D Magnetic resonance
  images of three weeks old barley roots grown in different soils"_. **License CC-BY-4.0 (verified via
  DataCite `rightsList`)** → public-safe. Format: NIfTI volumes (~2.1 GB total; we ingest one
  representative volume). Chosen because MRI is a brand-new sensor type for the arena (maximum
  novelty for the "novel modality" tier), it is a real cereal plant organ, and NIfTI is the cleanest
  volumetric format (carries voxel spacing → correct real-world mesh scale).
- **Subject home:** a **new spotlight** — `Hordeum vulgare — barley root system (3D MRI)` — explicitly
  framed as the volumetric-modality pilot and a cereal stand-in for the (logged) maize gap. It is not
  placed on the maize page (off-subject).
- **Alternatives considered (not chosen):** wheat/barley root CT (Harvard Dataverse `10.7910/DVN/DXG4AH`,
  CC0, but headerless RAW + soil/root thresholding); barley seed CT (Dryad CC0 — high-contrast/easy
  mesh + demeter lineage tie, but it is seeds, and the Zenodo mirror `5547948` turned out to be a 72 MB
  processed `demeter.zip`, not raw CT volumes, so raw volumes would need Dryad re-verification).

## Architecture

Mirrors the existing scan plumbing: a small pure-conversion module + a thin ingest script + a registry
entry + a source-class rule + a subject. Each unit is independently testable.

### `app/volume_convert.py` (new — mirrors `points_convert.py`)

Pure function, no DB I/O.

```
volume_to_glb(src_path, *, threshold=None, max_faces=200_000, step_size=1) -> bytes
```

- **Read** the volume into a numpy 3-D array + voxel spacing:
  - NIfTI (`.nii`/`.nii.gz`) via `nibabel` (spacing from the affine).
  - TIFF z-stack (`.tif`/`.tiff`, single multipage file OR a directory of slices) via `tifffile`
    (spacing defaults to isotropic 1.0 unless provided).
  - Format dispatch by extension; unsupported → `VolumeConvertError`.
- **Threshold** to a binary occupancy volume: Otsu (`skimage.filters.threshold_otsu`) by default; a
  caller-supplied absolute or percentile threshold overrides it. (MRI root-in-soil is low-contrast,
  so the threshold is the main quality knob — exposed, with an honest "approximate" caveat.)
- **Surface:** `skimage.measure.marching_cubes(volume, level, spacing=spacing, step_size=...)` →
  vertices/faces → `trimesh.Trimesh`. `step_size` downsamples huge volumes to keep runtime/poly sane.
- **Decimate** to `max_faces` via `simplify_quadric_decimation` (reuse the `mesh_convert` budget idiom).
- **Export** GLB; empty mesh (threshold yields no surface) → `VolumeConvertError`.
- New dependencies: `scikit-image` (+ `scipy`), `nibabel`, `tifffile`. (Tier 3 is explicitly new
  plumbing; all three ship manylinux wheels.)

### `app/sourcing.py`

- New source class **`volumetric`** (a 5th, alongside `ai`/`procedural`/`scan`/`found`):
  `source_class()` returns `"volumetric"` for sources starting with `mri:` or `ct:` (or in a
  `VOLUMETRIC_SOURCES` set). This keeps the new sensor axis visibly distinct in the spotlight grid.
- New `VOLUMETRIC_DATASETS` registry (parallel to `SCAN_DATASETS`): name, license, attribution, url
  for `ipk-barley-mri` (CC-BY-4.0; IPK e!DAL; DOI URL).

### `scripts/source_volumetric.py` (new — mirrors `source_scans.py`)

- `ingest_volumetric(db, volume_paths, *, dataset, to_glb, score_fn=None, task_title, modality, limit)`
  — testable core: per volume, `to_glb` → `ingest.register_output` with `source=f"{modality}:{dataset}"`
  (e.g. `mri:ipk-barley-mri`), `meta={depiction:"root_system", dataset, modality:"MRI", render:"mesh"}`,
  license/attribution/external_url from `VOLUMETRIC_DATASETS`. Mirrors `ingest_scans` error handling
  (one bad volume never aborts; scoring best-effort).
- `main()`: `--dataset`, `--task` (subject selector), `--dir` (local volume dir), `--threshold`,
  `--max-faces`, `--step`, `--no-score`. Acquisition recipe (e!DAL download) documented in the module
  docstring; one representative NIfTI volume is enough for the pilot.

### `app/spotlight.py` + subject seed

- New `SPOTLIGHTS` entry: slug `barley-mri`, `task_title="Hordeum vulgare — barley root system (3D MRI)"`,
  `featured=False`, blurb explaining the volumetric pilot + cereal-stand-in framing, `reference_image=None`.
- A seeded `Task` with that title (so the ingest core's `Task.title` lookup resolves).

## Data flow

e!DAL NIfTI volume → `source_volumetric.main()` → `volume_to_glb` (read → Otsu threshold → marching
cubes → decimate → GLB) → `ingest.register_output` (source `mri:ipk-barley-mri`, class `volumetric`) →
attached to the barley-MRI subject Task → surfaced on the new spotlight, badged as the volumetric/MRI
sensor axis.

## Error handling

- `VolumeConvertError` for: unsupported format, unreadable volume, threshold yields empty surface,
  empty GLB export. Ingest core catches per-volume and continues (mirrors `ingest_scans`).
- Scoring (`recon_service.score_and_store`) is best-effort; a scorer failure leaves the volume hosted.

## Testing

- `tests/test_volume_convert.py`: synthetic small 3-D numpy array (e.g. a solid sphere written to a
  temp `.tif` and a temp `.nii`) → `volume_to_glb` returns a valid, non-empty GLB with faces; explicit
  threshold path; empty-volume → `VolumeConvertError`; format-dispatch (nii vs tif vs unsupported).
- `tests/test_source_volumetric.py`: ingest core routes to the barley-MRI Task, sets `source` so
  `source_class` → `volumetric`, records CC-BY provenance, depiction `root_system`; skip/scoring-failure
  paths. Use the shared-DB-safe pattern (get-or-create the subject Task; scope `ModelOutput` asserts by
  (source, task_id); no bare `.one()` on `Task.title`).
- `tests/test_sourcing.py` (extend): `source_class("mri:x")` and `source_class("ct:x")` → `volumetric`.
- Real ingest of one barley MRI NIfTI volume into the worktree DB; full suite stays green.

## Honesty / caveats

- Barley, not maize — a cereal stand-in, labeled as such; the maize volumetric gap remains logged.
- Root-system MRI is low-contrast; the marching-cubes mesh is an **approximate** ground-truth-ish
  reference (threshold-dependent), not a polished asset. The spotlight blurb + the output caveat say so.

## Out of scope (YAGNI)

- DICOM and RAW readers (add when a dataset needs them — pick the two formats our pilot + likely next
  datasets use: NIfTI + TIFF).
- Volume re-segmentation / denoising beyond a single threshold.
- 4D / temporal volumetric (Track B, deferred).
- Ingesting the full barley MRI set or multiple volumes — one representative volume for the pilot.

## Acceptance criteria

1. `volume_to_glb` converts a NIfTI and a TIFF-stack volume to a valid GLB (unit-tested).
2. `source_class` returns `volumetric` for `mri:`/`ct:` sources (unit-tested).
3. One real barley root MRI volume (CC-BY-4.0) is ingested as `mri:ipk-barley-mri` on the new
   barley-MRI subject, with correct provenance, and appears on its spotlight badged as the volumetric
   sensor axis.
4. Full test suite green.
