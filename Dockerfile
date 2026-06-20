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

# Seed on first boot if empty, then serve. Seed is idempotent.
CMD ["sh", "-c", "python -m app.seed; uvicorn app.main:app --host 0.0.0.0 --port 8000"]
