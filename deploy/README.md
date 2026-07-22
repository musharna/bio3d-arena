# Bio 3D Arena — public-instance deploy runbook

The public instance is a **separate, self-contained deployment**. It never talks to
the internal (Agrigen-coupled) instance at runtime: no live scoring, no shared GT
bundle directory, no shared database. Everything the public instance serves is
promoted ahead of time from the internal instance via an export/import bundle.

Scoring is OFF on the public instance (`BIO3D_RECON_SCORER_URL=` empty in
`.env.public.example`) — scores shown publicly are the ones promoted in the bundle,
never recomputed live.

## 1. Export (run on the internal instance)

```bash
python -m scripts.export_public \
  --tasks "task-a,task-b,task-c" \
  --generators "generator-x,generator-y" \
  --out public_bundle/v1
```

Review the **printed manifest** before doing anything else with the bundle:

- license breakdown (only allowlisted licenses may leave the internal instance)
- row counts per table (tasks, generators, outputs, gold pairs, votes, scores)
- any skipped/excluded records and why

If the manifest looks wrong (unexpected license, unexpected row counts), stop —
do not transfer. Re-run with a narrower `--tasks`/`--generators` selection, or use
`--dry-run` first to inspect without writing.

## 2. Transfer the bundle

Copy `public_bundle/v1` (the whole directory) from the internal host to the public
host, e.g.:

```bash
rsync -avz public_bundle/v1 <public-host>:/srv/bio3d-arena/public_bundle/v1
```

The bundle is self-contained: baked GT GLBs, promoted scores, and a manifest with
checksums. It does not reference any internal filesystem path (no Agrigen path, no
`BIO3D_GT_BUNDLE_DIR`).

## 3. Import (run on the public instance)

Load the public env (see `deploy/.env.public.example`) and import:

```bash
export $(grep -v '^#' deploy/.env.public.example | xargs)  # or your host's secret-store equivalent
python -m scripts.import_public --bundle public_bundle/v1
```

Import verifies bundle checksums and fails loud on mismatch — do not proceed on a
partial or corrupted transfer.

## 4. Install dependencies and boot the app

Install the **runtime** set only:

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`requirements.txt` is deliberately just what serving requires. Do **not** install
`requirements-research.txt` or `requirements-dev.txt` on the public host: the research set pulls
`open_clip_torch` → `torch` → the full NVIDIA CUDA stack (cublas, cudnn, nccl, cusolver), which the
web app never imports. Measured on a clean venv: **173 MB runtime-only vs 5.6 GB** with the
research stack.

Nothing is lost by omitting it — scoring and judging are offline operations that run on the
internal instance, and all scores are promoted in the bundle. `tests/test_runtime_deps.py` boots
the real ASGI app in a subprocess and fails if serving a public route ever imports the research
stack, so this stays true.

Use the environment values from `deploy/.env.public.example`, with real secrets
filled from the host's secret store (never commit real values):

- `BIO3D_DATABASE_URL` — managed Postgres (see free-tier targets below)
- `BIO3D_STORAGE_BACKEND=s3` + `BIO3D_S3_BUCKET` + `BIO3D_S3_PUBLIC_BASE_URL` —
  object storage for assets
- `BIO3D_ADMIN_TOKEN` — a freshly rotated long random secret, never the dev
  placeholder
- `BIO3D_REQUIRE_CAPTCHA=true` + `BIO3D_CAPTCHA_PROVIDER=turnstile` +
  `BIO3D_CAPTCHA_SECRET` — bot protection on public vote/submit endpoints
- `BIO3D_RECON_SCORER_URL=` — left empty; scoring stays off on the public instance

## 5. Smoke test

Hit each of the following and confirm a 200 with sane content:

- `/`
- `/leaderboard`
- `/arena`
- `/coverage`
- `/terms`
- `/licenses`

Then confirm the internal research pages are **404**, not 200 — with scoring off,
`INTERNAL_PAGES_ENABLED` is false and they must hard-404 rather than render:

- `/benchmark`

(`/benchmark` was previously listed above as a route to confirm 200, which was wrong: it is an
internal page and 404s under the public posture, so the smoke test failed against a correctly
configured instance.)

## Free-tier hosting targets

- **App**: Fly.io or Render (either works; pick whichever the deployer already has
  an account on — nothing in the app is Fly- or Render-specific)
- **Postgres**: Neon or Supabase
- **Assets (S3-compatible)**: Cloudflare R2

## What the public instance deliberately does not have

- No path into the internal Agrigen filesystem (no `/home/mjarnold/agrigen` anywhere
  in config)
- No `BIO3D_GT_BUNDLE_DIR` — GT GLBs are pre-baked into the imported bundle's asset
  store, not read live from an internal directory
- No live recon scorer — `BIO3D_RECON_SCORER_URL` is empty; all displayed scores were
  promoted at export time
- No shared database or shared admin token with the internal instance
