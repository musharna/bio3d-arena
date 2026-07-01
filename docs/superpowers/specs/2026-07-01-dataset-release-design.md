# Dataset Release (SP3-thin) — design

> 2026-07-01. The one genuinely-missing piece of SP3 (the rest — submission UI, governance page,
> public API — already exists). Makes the arena's biological-3D benchmark a **citable, versioned,
> licensed, downloadable dataset**. Depends on SP1's `scripts/export_public.py` (open PR #1).

## Goal

Package the arena's benchmark as a release an external researcher can download, cite, and run
their own generators against — without leaking the held-out test set or unpublished/unlicensed
work. Framing = **benchmark** (tasks + input photos + 3D outputs + rendered GT reference +
metrics + leaderboard snapshot); human-preference records ride along as a documented secondary
file (they exist already at `/api/export.json`), NOT the headline (vote volume is still low).

## Non-goals (YAGNI)

- No new export pipeline — reuse SP1's `export_public.py` bundle verbatim.
- No preference-dataset framing / reward-model tooling (revisit post-soft-launch when votes scale).
- No DOI minting / Zenodo upload automation (manual for v1; the release is a self-contained tarball).
- No raw held-out GT (`.npy`) — same integrity boundary as SP1.

## Dependency

Consumes SP1's `scripts/export_public.py` → `export_bundle(...)` which emits
`out_dir/{rows.json, assets/…, gt/…, manifest.json}` (curated, license-gated, referentially
complete, no `.npy`, no agrigen path). This spec adds only the citation/licensing/docs layer.

## Components

### 1. `scripts/build_dataset_release.py`

Orchestrates a release:

- Calls `export_bundle(db, storage, task_titles=…, generator_slugs=…, out_dir=<release>/bundle)`
  (the curated benchmark — reuses SP1's fail-loud license gate + leak boundary).
- Writes **`LICENSE`** — an aggregate statement + a per-output attribution roll-up generated from
  the distinct `(license, attribution, source)` tuples of the included `ModelOutput`s (same data
  the `/licenses` page renders). Fails loud (reuses SP1's gate) if any included output lacks a
  redistributable license.
- Writes **`DATASHEET.md`** — datasheets-for-datasets style: what's in it (counts from the
  manifest), taxa/tasks covered, how outputs were generated + scored, the **held-out-GT-private**
  note, metric definitions (chamfer/F-score are _reference_ not the sole ranking), and known
  limitations (low human-vote volume; provisional vs firm generators per `/coverage`).
- Writes **`VERSION`** — a caller-supplied version string (e.g. `2026.07-v1`) + the bundle's
  `manifest.sha256` (content hash) for citeability. (Version is passed in, not generated — scripts
  can't call time/random.)
- Writes **`preference_records.json`** — the `/api/export.json` payload (`{n_votes, votes:[…]}`)
  as the documented secondary artifact.
- Emits the whole thing under `data/releases/<version>/` and a `<version>.tar.gz`.

### 2. `/dataset` landing page (`app/main.py` route + `app/templates/dataset.html`)

Describes the current release: version, contents summary (from `DATASHEET`/manifest), the license

- **how to cite**, a download link to the tarball, and pointers to the live API
  (`/api/export.json`, `/openapi.json`). Lists released versions if more than one exists.

### 3. Held-out GT policy (unchanged from SP1)

Ships baked GT _reference render_ GLBs (`gt/…`), never raw `.npy` point clouds. The datasheet
states this explicitly so users know the ranking GT is withheld for integrity.

## Data flow

```
curate allowlist (task titles + generator slugs)
        │
        ▼  scripts/build_dataset_release.py --version 2026.07-v1 --tasks … --generators …
 export_bundle (SP1) ──► <release>/bundle/{rows.json, assets/, gt/, manifest.json}
        │ decorate
        ├─ LICENSE               (aggregate + per-output attribution roll-up; fail-loud)
        ├─ DATASHEET.md          (contents, GT-private note, limitations)
        ├─ VERSION               (version string + manifest.sha256)
        └─ preference_records.json  (/api/export.json payload)
        ▼
 data/releases/<version>/ + <version>.tar.gz   ──►  /dataset serves the link
```

## Error handling / failure modes

- **Unlicensed included output** → abort (loud), naming the output id (reuses SP1 `check_licenses`).
- **No allowlisted tasks/generators** → abort with a clear message (empty release is a mistake).
- **`.npy` or `/home/mjarnold/agrigen` in the release tree** → the build asserts their absence and
  aborts if found (defense-in-depth over SP1's guarantees).
- **`/dataset` with no release built yet** → 200 with a "no release published" state, not a 500.

## Testing

- **Unit**: `build_dataset_release` on a seeded temp DB (reuse the `db_session` fixture + `_mk`
  helper from SP1's tests) produces a release dir containing `LICENSE`, `DATASHEET.md`, `VERSION`
  (with the manifest sha256), `preference_records.json`, and the `bundle/`.
- **Leak assertions** (real-execution): the release tree contains zero `.npy` and no
  `/home/mjarnold/agrigen`; every included output's license is in the redistributable allowlist.
- **Fail-loud**: a release including an unlicensed external output raises (reuses SP1 gate).
- **`/dataset` route**: 200 HTML when a release exists AND when none exists (no-release state).

## Open decisions (defaults chosen)

1. **Include votes at all?** Default: yes, as `preference_records.json` (cheap, already exists),
   but framed as secondary in the datasheet. Revisit if privacy of `session_id` matters — default
   is to keep the opaque session ids as-is (they're already in `/api/export.json`).
2. **Release storage** — under `data/releases/` (gitignored, like other data). The tarball is the
   artifact; not committed to git.
3. **Versioning** — caller passes `--version`; no auto-DOI in v1.

## Out of scope

Zenodo/DOI automation, preference-dataset reward-model tooling, generator-level submission flow
(all deferred). SP2 (verified login) is the next sub-project after this.
