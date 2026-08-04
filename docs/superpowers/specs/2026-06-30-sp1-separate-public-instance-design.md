# SP1 — Separate Public Instance (design)

> 2026-06-30. First product move in the [Go-Public Roadmap](2026-06-30-bio3d-arena-go-public-roadmap.md).
> Goal: stand up a public Taxon3D that is **self-contained** (no Agrigen dependency),
> **cheap to run**, and **cannot leak** unpublished Agrigen work or the held-out test set.

## Problem

The arena currently runs against Agrigen's internal machine. Three couplings block a public deploy:

1. **GT bundle** — `config.GT_BUNDLE_DIR` → `/home/mjarnold/agrigen/backend/data/gt_bundle_prod`.
   Read at build-time by `app/gt_render.py:bake_species_gt` to render the reference GT GLBs shown
   in `/benchmark`. The raw `(N,3)` `.npy` point clouds are also the scorer's **held-out test set**.
2. **Scorer** — `config.RECON_SCORER_URL` → Agrigen's `scoring_service` (live Chamfer/F-score).
   Called by `app/recon_service.py` + `app/structure_service.py` (scorer is injectable).
3. **Data** — the internal DB holds unpublished tasks/outputs and outputs whose licenses don't
   permit redistribution.

Publishing the GT scans would also **leak the benchmark test set** (a reconstructed model could
be tuned against public GT), so raw GT must stay internal.

## Core principle: promote, don't recompute

The public instance is a **read-mostly consumer of a curated export**. It:

- runs **no scorer** (`RECON_SCORER_URL` unset → scoring disabled; shows _promoted_ scores),
- reads **no Agrigen path** (`GT_BUNDLE_DIR` unset → serves _pre-baked_ GT reference GLBs shipped
  as assets; raw point clouds never leave the internal instance),
- receives only **publishable** rows (license-filtered, published-task-filtered).

The code already supports this: `bake_species_gt(bundle_dir=...)` is parameterized and the
service scorers take `scorer=...`. The new work is a **promotion pipeline** + a **scoring-disabled
mode** + config/deploy, not a rewrite.

## Components

### 1. Export pipeline (internal instance) — `scripts/export_public.py`

Reads the internal study DB + assets, emits a portable **public bundle**:

- **DB rows**: Categories, Criteria, Tasks, Generators, ModelOutputs, Comparisons, Votes, Ratings —
  **filtered** by a **curated promotion allowlist**. Note the schema has **no `is_published`/
  internal flag today**: `Task` has only `active` (bool), `Generator` has `kind`
  (model|human|baseline) + `is_anonymous` — no visibility column. So promotion is **explicit**:
  the export takes an allowlist of task titles + generator slugs to publish (nothing is public
  unless named — matches the fail-loud, no-silent-inclusion boundary). Additional hard gates:
  (a) `Task.active == True`, (b) `ModelOutput.is_gold == False` (gold decoys never leave the
  internal instance), (c) `ModelOutput.license` in a redistributable-license allowlist
  (CC-BY/CC0/etc.). _Alternative if the allowlist proves unwieldy: add a nullable `is_public`
  column to Task + Generator via a small migration — deferred until the allowlist hurts._
- **Assets**: the GLB blobs for included outputs + their thumbnails/renders.
- **GT references**: the **baked** GT GLBs (`gt_render.bake_species_gt`) for included species —
  NOT the raw `.npy` clouds. Baking runs here, against the internal `GT_BUNDLE_DIR`.
- **Scores**: precomputed Metric rows (chamfer/F-score) copied as static values.
- Output: a versioned dir/tarball (`public_bundle/<version>/…`) + a `manifest.json`
  (counts, license summary, provenance) for auditability.

**Safety:** the export is the single chokepoint for the leak boundary. It **fails loud** if any
included output has a null/unknown license (no silent inclusion). A `--dry-run` prints the
include/exclude tally + license breakdown without writing.

### 2. Import pipeline (public instance) — `scripts/import_public.py`

Loads a public bundle into a fresh public DB + storage backend. Idempotent per bundle version.
Verifies the manifest checksum before load; refuses a bundle with unknown-license rows.

### 3. Scoring-disabled mode

- `config.SCORING_ENABLED = bool(RECON_SCORER_URL)` (or an explicit flag).
- When disabled: `recon_service`/`structure_service` skip live scoring; `/benchmark` and score
  columns read the promoted Metric rows. No outbound calls to any scorer.
- `gt_render.find_gt_glb` serves the imported baked GLBs; `GT_BUNDLE_DIR` is never dereferenced.

### 4. Deploy target (cheap-now, scale-as-needed)

Recommended stack (all have real free tiers; each is a config switch already in `config.py`):

- **App**: containerized FastAPI (Dockerfile exists) on **Fly.io** or **Render** free/hobby tier.
  _(HF Spaces is a strong discoverability mirror but awkward for persistent Postgres — note as a
  later mirror, not the primary.)_
- **DB**: **Neon** or **Supabase** free Postgres → `BIO3D_DATABASE_URL=postgresql+psycopg://…`.
- **Assets**: **Cloudflare R2** (zero egress) via the existing `S3StorageBackend`
  (`BIO3D_STORAGE_BACKEND=s3` + bucket + `S3_PUBLIC_BASE_URL` CDN domain).
- Cold-start/idle is acceptable at this stage.

### 5. Hardening (mostly config)

- **Rotate** `BIO3D_ADMIN_TOKEN` (default is `changeme-admin-token`) — secret via host env, never
  committed.
- **Captcha**: implement the real Turnstile/hCaptcha check in `integrity.verify_captcha` (currently
  a no-op stub) + set `BIO3D_REQUIRE_CAPTCHA=true` + keys.
- Confirm the audit-fixed admin surfaces (`require_admin_query` on `/admin`, `/admin/moderation`)
  hold under the prod config.
- **Legal pages**: ToS, privacy (what session data is stored), and an attribution/licenses page
  generated from the bundle manifest (`ModelOutput.attribution`/`license`).

## Data flow

```
INTERNAL (Agrigen machine)                         PUBLIC (Fly/Render)
  study DB + assets + GT_BUNDLE_DIR                  public DB (Neon) + R2 assets
        │                                                   ▲
        │  export_public.py                                 │  import_public.py
        │  • license/publish filter (fail-loud)             │  • checksum verify
        │  • bake GT GLBs (raw .npy stays here)             │  • refuse unknown-license
        │  • copy precomputed scores                        │
        ▼                                                   │
   public_bundle/<version>/  ──────── transfer ────────────┘
   (DB rows + GLBs + baked GT + scores + manifest)

  Public instance: SCORING_ENABLED=False, GT_BUNDLE_DIR unset, RECON_SCORER_URL unset.
```

## Error handling / failure modes

- **Unknown-license output** → export aborts (loud), names the output id. No silent drop, no
  silent include.
- **Missing asset blob** for an included row → export aborts with the path (reuse the audit
  tooling that found 0 missing / 314 orphans).
- **Public instance receives a scoring request** → returns "scoring disabled on public instance"
  rather than dialing a scorer.
- **Bundle checksum mismatch on import** → refuse load.

## Testing

- **Unit**: export filter (license allowlist include/exclude; null-license → raises); scoring-
  disabled service paths return promoted scores and make no outbound call (assert scorer never
  invoked).
- **Round-trip (real-execution)**: `export_public.py` on a **copy** of the study DB → fresh temp
  DB + temp storage via `import_public.py` → assert row counts match the filtered set, every
  included GLB present, GT GLBs baked, no `/home/mjarnold/agrigen` string anywhere in the public
  bundle or public config. (Per real-execution-at-boundaries doctrine — the leak boundary gets a
  real filesystem walkthrough, not just a mocked filter.)
- **Leak assertion**: grep the emitted bundle for raw `.npy` GT and for internal-only generator
  names → must be absent.
- **Smoke**: boot the public instance against the imported bundle with `GT_BUNDLE_DIR`/
  `RECON_SCORER_URL` unset; `/`, `/leaderboard`, `/benchmark`, `/coverage` all 200; a vote records.

## Open decisions (surface before/at implementation)

1. **Held-out GT policy** — keep GT scans private (recommended: preserves benchmark integrity,
   public sees only baked reference render), OR release GT as an open dataset in SP3 (makes it
   "useful" but converts the benchmark to a static known-test). Default here: **private**; revisit
   in SP3.
2. **Vote data in export** — ship historical internal votes to seed the public leaderboard, or
   start the public vote pool clean? (Trust/gold-check provenance may not transfer cleanly.)
   Default: **start clean**, keep internal ratings as a labeled "internal" snapshot.
3. **Deploy host** — Fly vs Render (both fine; pick on free-tier limits at build time).

## Out of scope

Verified login (SP2), public submission UI (SP3), dataset release (SP3), the paper (SP4).
