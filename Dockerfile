FROM python:3.12-slim

# trimesh needs nothing heavy for GLB export; keep the image lean.
WORKDIR /app

# Runtime deps PLUS the scale backends. The public deploy is configured for managed Postgres
# and S3-compatible object storage (deploy/README.md) — BIO3D_DATABASE_URL=postgresql://... and
# BIO3D_STORAGE_BACKEND=s3 — and neither driver ships in requirements.txt. An image built from
# requirements.txt alone booted and could reach neither its database nor its assets.
#
# This does NOT undo the runtime/research split: requirements-scale.txt is psycopg + boto3 +
# redis, a few MB. The split exists to keep torch and the NVIDIA CUDA stack (5.6 GB) off the web
# host, and requirements-research.txt is still deliberately absent here.
COPY requirements.txt requirements-scale.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-scale.txt

COPY app ./app

# Runtime data (SQLite DB + assets) lives on a mounted volume.
ENV BIO3D_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000

# Serve. Nothing else.
#
# This used to run `python -m app.seed` first, with a comment calling the seed idempotent. It
# was not: that entry point defaulted to force=True, which WIPES every seeded table. Containers
# restart for ordinary reasons — deploys, failed health checks, host migrations — so a public
# instance would have destroyed its own votes on every restart, and votes are the one
# irreplaceable thing here. The entry point now defaults to non-destructive (app/seed.py), and
# the boot path does not seed at all.
#
# A public instance gets its data from the import bundle (deploy/README.md), never from the demo
# seeder, so seeding on boot would be wrong even now that it is safe. Seeding is an explicit
# operator action: `python -m app.seed` for a local demo, `--force` to reset one.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
