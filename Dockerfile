FROM python:3.12-slim

# trimesh needs nothing heavy for GLB export; keep the image lean.
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
