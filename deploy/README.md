# Taxon3D — public-instance deploy runbook

The public instance is a **separate, self-contained deployment**. It never talks to
the internal (Agrigen-coupled) instance at runtime: no live scoring, no shared GT
bundle directory, no shared database. Everything the public instance serves is
promoted ahead of time from the internal instance via an export/import bundle.

Scoring is OFF on the public instance (`BIO3D_RECON_SCORER_URL=` empty in
`.env.public.example`) — scores shown publicly are the ones promoted in the bundle,
never recomputed live.

## Which database is the release built from?

**`data/study/arena-study.db` — always.** Step 1 exports from whatever DB the internal
instance is pointed at, and that must be the study DB.

`data/arena-preview.db` is **not** a release artifact. It is a hand-refreshed mirror of study
used for previewing, it is not gate-filtered (it carries the same generators and tasks study
does, including app-hidden ones), and nothing in this runbook reads it. Treat it as scratch.

This matters because hand-refreshed mirrors drift. On 2026-07-27 the preview mirror was found
178 votes behind study and still holding 8 outputs whose GLBs had been deleted — it had missed
both a vote migration and a corpus deletion. Nothing shipped from it, because the release path
is the export below; but anyone who assumed "preview == what we publish" would have shipped a
leaderboard computed on 60% of the votes.

If you refresh the mirror anyway, use `sqlite3`'s backup API (`.backup` / `Connection.backup`),
never `cp` — copying a WAL database with `cp` silently drops whatever is still in the `-wal`,
which is how 80 rescued votes were lost on 2026-07-26.

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

### The operator env is NOT the app env — R2 names have to be mapped

The secret file an operator holds (`~/.bio3d-deploy.env` on the release machine) stores the object
storage credentials under **Cloudflare's own names**, while the app and boto3 read the
**AWS/BIO3D** names. Nothing translates between them, so sourcing the secret file and running the
import straight away leaves `BIO3D_STORAGE_BACKEND` unset, which resolves to local storage. The
import now **refuses** in that state rather than writing the blobs to local disk and reporting
complete success while uploading nothing — but the refusal only tells you the mapping is missing.
This is the mapping:

```bash
set -a; . ~/.bio3d-deploy.env; set +a
export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export AWS_ENDPOINT_URL="$R2_ACCOUNT_ENDPOINT"   # needs botocore >= 1.34
export AWS_DEFAULT_REGION=auto                   # R2 ignores the value but boto3 demands one
export BIO3D_STORAGE_BACKEND=s3
export BIO3D_S3_BUCKET="$R2_BUCKET"
```

Verify the mapping **before** an upload, with a read: a HEAD against a key you know exists proves
credentials, endpoint and bucket in one call, and cannot write anything.

```bash
python -c "import boto3,os; print(boto3.client('s3').head_object(
  Bucket=os.environ['BIO3D_S3_BUCKET'], Key='reference/gallery/zea_mays/1.jpg')['ContentLength'])"
```

Neither `boto3` nor `psycopg` is in `requirements.txt` — that set is deliberately what _serving_
needs. Both are pinned in `requirements-scale.txt`; install from there on the release machine.

### Then import

Load the public env (see `deploy/.env.public.example`) and import:

```bash
export $(grep -v '^#' deploy/.env.public.example | xargs)  # or your host's secret-store equivalent
python -m scripts.import_public --bundle public_bundle/v1
```

Import verifies bundle checksums and fails loud on mismatch — do not proceed on a
partial or corrupted transfer. It also refuses to run against local storage unless you pass
`--local-assets`, which is how you rebuild a local preview from a bundle on purpose.

The import has two phases: rows into the database, then blobs into storage. The blob phase is
**resumable** — it skips objects already present (a HEAD, not a PUT) and retries transient
transport faults — so a broken transfer is re-run, never restarted. Re-run the blob phase alone
with:

```bash
python -m scripts.import_public --bundle public_bundle/v1 --assets-only
```

This matters more than it sounds. A release bundle is multiple GB over whatever link the
operator has, and on the first real deploy the upload died ~40 minutes in on a single corrupted
TLS record (`SSLV3_ALERT_BAD_RECORD_MAC`). Nothing was wrong with the bundle or the credentials.

`--assets-only` is also the right flag when only the blobs changed. Its opposite case — promoting
recomputed leaderboards without moving 4 GB of unchanged meshes — is just a normal import: the
row phase rewrites the boards and the blob phase skips every object it already finds.

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
- `BIO3D_GOOGLE_SITE_VERIFICATION` / `BIO3D_BING_SITE_VERIFICATION` — ownership tokens from
  Search Console / Bing Webmaster Tools. Optional; unset renders no tag at all (an ownership
  `<meta>` with `content=""` is a malformed claim, so there is no safe default)
- `BIO3D_INDEXNOW_KEY` — 8–128 chars of `[a-zA-Z0-9-]`; generate with
  `python -c "from app.indexnow import generate_key; print(generate_key())"`. Serves the key
  file at `/indexnow-key.txt`, which is how IndexNow verifies domain ownership. Unset means the
  key file 404s and `scripts/submit_indexnow.py` refuses to run — deliberately, because the
  alternative is a 403 from the API several steps removed from the cause

## 4b. Search-engine submission (optional, after the first deploy)

`robots.txt` advertises the sitemap, so a crawler that already knows the site will find it.
These steps tell the search engines the site exists and report back on whether it worked:

1. **Search Console** — add the property at `search.google.com/search-console`, choose the
   HTML-tag method, put the token in `BIO3D_GOOGLE_SITE_VERIFICATION`, redeploy, then verify
   and submit `/sitemap.xml`.
2. **Bing Webmaster Tools** — `bing.com/webmasters`; it can import the Search Console property
   directly, or use `BIO3D_BING_SITE_VERIFICATION` the same way.
3. **IndexNow** — set `BIO3D_INDEXNOW_KEY`, redeploy, then
   `python scripts/submit_indexnow.py --base-url https://<domain>`. One POST reaches Bing,
   Yandex, Seznam and Naver; no account needed. Re-run after a deploy that adds or changes
   URLs. `--dry-run` prints the payload without sending it.

## 4c. Citable DOI via Zenodo (optional)

**Order matters and is not recoverable:** Zenodo only mints a DOI for releases created _after_
the GitHub integration is switched on. Enabling it later does not pick up existing releases —
those have to be uploaded manually, oldest first.

1. Sign in at `zenodo.org` with GitHub and authorise it.
2. Under _GitHub_, flip the switch for the repository. (It must be public.)
3. **Then** cut the release: `git tag -a v0.1.0 -m "..." && git push origin v0.1.0`, and
   publish it on GitHub.
4. Zenodo mints the DOI within a few minutes. Add the badge to `README.md` and the DOI to
   `CITATION.cff`.

Note what this DOIs: the **repository** — code, and the docs in it. It is not a DOI for the
corpus, which has no public distribution yet (`/dataset` currently describes a release that is
not downloadable). A dataset DOI is a separate, larger job.

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

## Never recompute on the public instance

Do not call `/admin/recompute` or `/admin/recompute_judge` against a public deploy. The
Bradley-Terry fit runs a 200-sample bootstrap (`config.BT_BOOTSTRAP`) while holding the rating
tables in memory; on a 1 GB web machine, inside an HTTP request, it OOM-killed the VM ("Virtual
machine exited abruptly") after ~220 seconds.

It also should never be necessary. Every board the public site renders — global, per-kingdom,
and both AI-judge boards — is **promoted in the bundle**, fitted on the internal instance where
the database is local. If a board looks stale, the fix is to re-run the recompute internally and
export a new bundle, not to compute anything in production.

## Free-tier hosting targets

- **App**: Fly.io or Render (either works; pick whichever the deployer already has
  an account on — nothing in the app is Fly- or Render-specific)
- **Postgres**: Neon or Supabase
- **Assets (S3-compatible)**: Cloudflare R2

The live deployment uses Fly (`fly.toml`, committed) + Neon + R2. Two settings there are easy to
get wrong and are worth repeating for any other host:

- **Trust the proxy's forwarded-for header** (`BIO3D_TRUST_FORWARDED_FOR=true` on Fly). Any
  platform that terminates TLS at an edge makes every request appear to come from the proxy.
  Without this the per-IP vote limiter sees ONE client, so every visitor on earth shares a single
  300-per-60s bucket — useless against a farmer, and actively harmful to real voters.
- **Give the machine 1 GB, not 512 MB.** Assets stream _through_ the app on remote storage (so
  the object key never leaks to the client — see `media_asset`), and the corpus contains meshes
  up to ~124 MB.

## What the public instance deliberately does not have

- No path into the internal Agrigen filesystem (no `/home/mjarnold/agrigen` anywhere
  in config)
- No `BIO3D_GT_BUNDLE_DIR` — GT GLBs are pre-baked into the imported bundle's asset
  store, not read live from an internal directory
- No live recon scorer — `BIO3D_RECON_SCORER_URL` is empty; all displayed scores were
  promoted at export time
- No shared database or shared admin token with the internal instance
