"""Load a curated public bundle into a fresh public DB + storage (SP1)."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect as sqla_inspect  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base, engine_kwargs  # noqa: E402
from app.storage import StorageBackend, get_storage  # noqa: E402
from scripts.export_public import EXPORT_MODELS  # noqa: E402

_BY_TABLE = {m.__tablename__: m for m in EXPORT_MODELS}


class BundleChecksumError(RuntimeError):
    pass


def _coerce_datetimes(model, d: dict) -> dict:
    """Task 3 serialized DateTime columns via ``.isoformat()`` -> plain strings.

    SQLite's SQLAlchemy DateTime type expects a ``datetime.datetime`` (or None) on
    insert, not a raw ISO string, so parse known DateTime columns back before merge.
    """
    dt_cols = {
        c.key
        for c in sqla_inspect(model).mapper.column_attrs
        if c.columns[0].type.__class__.__name__ == "DateTime"
    }
    out = dict(d)
    for k in dt_cols:
        v = out.get(k)
        if isinstance(v, str):
            out[k] = dt.datetime.fromisoformat(v)
    return out


def _bundle_assets(b: Path):
    """(relative storage key, local file) for every blob in the bundle, in a stable order."""
    for sub in ("assets", "gt"):
        for p in sorted((b / sub).rglob("*")):
            if p.is_file():
                yield (str(p.relative_to(b / "assets")) if sub == "assets" else f"gt/{p.name}"), p


def _upload_assets(b: Path, storage: StorageBackend, *, attempts: int = 4) -> dict:
    """Upload bundle blobs, skipping what is already there and retrying what fails.

    A release bundle is multiple GB, and the upload is one long TLS session over whatever link
    the operator has. Treating that as all-or-nothing was wrong twice over: the first real
    release died 40 minutes in on a single `SSLV3_ALERT_BAD_RECORD_MAC` — one corrupted TLS
    record — and the only recovery on offer was to re-upload everything that had already
    landed, plus re-run the row import, because the two phases were welded together.

    So: `exists()` makes the pass resumable (re-running costs a HEAD per object, not a PUT), and
    the retry loop keeps a transient network fault from ending the run at all. Together they
    make the upload converge on a bad link instead of merely being likely to survive one.
    """
    sent = skipped = 0
    for rel, p in _bundle_assets(b):
        if storage.exists(rel):
            skipped += 1
            continue
        for attempt in range(1, attempts + 1):
            try:
                storage.save(rel, p.read_bytes())
                sent += 1
                break
            except Exception as e:  # noqa: BLE001 — any transport fault is worth one more try
                if attempt == attempts:
                    raise
                delay = 2**attempt
                print(
                    f"upload {rel} failed ({type(e).__name__}: {e}); "
                    f"retry {attempt}/{attempts - 1} in {delay}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
    return {"uploaded": sent, "already_present": skipped}


def import_bundle(
    bundle_dir, *, database_url: str, storage: StorageBackend, rows: bool = True
) -> dict:
    b = Path(bundle_dir)
    manifest = json.loads((b / "manifest.json").read_text())
    rows_bytes = (b / "rows.json").read_bytes()
    if hashlib.sha256(rows_bytes).hexdigest() != manifest.get("sha256"):
        raise BundleChecksumError(f"rows.json checksum != manifest for {b}")
    tables = json.loads(rows_bytes)

    counts = {}
    if rows:
        eng = create_engine(database_url, future=True, **engine_kwargs(database_url))
        Base.metadata.create_all(eng)
        with Session(eng) as s:
            for model in EXPORT_MODELS:  # FK-safe order
                name = model.__tablename__
                for d in tables.get(name, []):
                    s.merge(model(**_coerce_datetimes(model, d)))  # merge = idempotent by PK
                counts[name] = len(tables.get(name, []))
            s.commit()

    counts.update(_upload_assets(b, storage))
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument(
        "--assets-only",
        action="store_true",
        help="skip the row import and only sync blobs — for resuming an interrupted upload "
        "without replaying every row against a remote database again",
    )
    a = ap.parse_args()
    counts = import_bundle(
        a.bundle,
        database_url=__import__("app.config", fromlist=["DATABASE_URL"]).DATABASE_URL,
        storage=get_storage(),
        rows=not a.assets_only,
    )
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
