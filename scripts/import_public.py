"""Load a curated public bundle into a fresh public DB + storage (SP1)."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
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


def import_bundle(bundle_dir, *, database_url: str, storage: StorageBackend) -> dict:
    b = Path(bundle_dir)
    manifest = json.loads((b / "manifest.json").read_text())
    rows_bytes = (b / "rows.json").read_bytes()
    if hashlib.sha256(rows_bytes).hexdigest() != manifest.get("sha256"):
        raise BundleChecksumError(f"rows.json checksum != manifest for {b}")
    tables = json.loads(rows_bytes)

    eng = create_engine(database_url, future=True, **engine_kwargs(database_url))
    Base.metadata.create_all(eng)
    counts = {}
    with Session(eng) as s:
        for model in EXPORT_MODELS:  # FK-safe order
            name = model.__tablename__
            for d in tables.get(name, []):
                s.merge(model(**_coerce_datetimes(model, d)))  # merge = idempotent by PK
            counts[name] = len(tables.get(name, []))
        s.commit()

    for sub in ("assets", "gt"):
        base = b / sub
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(b / "assets")) if sub == "assets" else f"gt/{p.name}"
                storage.save(rel, p.read_bytes())
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    a = ap.parse_args()
    counts = import_bundle(
        a.bundle,
        database_url=__import__("app.config", fromlist=["DATABASE_URL"]).DATABASE_URL,
        storage=get_storage(),
    )
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
