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

from sqlalchemy import create_engine, inspect as sqla_inspect, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import config  # noqa: E402
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
    sent = skipped = replaced = 0
    for rel, p in _bundle_assets(b):
        # "Already there AND identical", not merely "already there". Re-publishing a mesh at a
        # key that already holds an older version is the NORMAL case for a release — an
        # exists()-only check skips every one of them, reports success, and changes nothing.
        # That would have silently discarded a 9.1x recompression of the whole corpus.
        local_size = p.stat().st_size
        remote_size = storage.size(rel)
        if remote_size == local_size:
            skipped += 1
            continue
        if remote_size is not None:
            replaced += 1
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
    return {"uploaded": sent, "already_present": skipped, "replaced": replaced}


def sync_id_sequences(conn) -> dict[str, int]:
    """Advance every id sequence to max(id) of the table it feeds. Returns {table: new_value}.

    `s.merge()` writes rows with EXPLICIT primary keys, and an explicit-id INSERT does not
    advance a Postgres sequence. So after an import each `<table>_id_seq` still sits where it
    started while the imported rows occupy ids far above it, and the FIRST insert that reaches
    an occupied id fails on a duplicate key. Nothing complains at import time — the live
    instance ran for hours with vote_id_seq at 1 against max(id)=461, i.e. 48 votes from
    500ing every /api/vote.

    SQLite derives the next rowid from max(rowid) and has no sequences, so this is a no-op
    there — which is precisely why the suite stayed green while production was broken.
    """
    if conn.dialect.name != "postgresql":
        return {}
    seqs = conn.execute(
        text("""
        select s.sequencename, s.last_value, t.relname as tbl, a.attname as col
        from pg_sequences s
        join pg_class sc on sc.relname = s.sequencename
        join pg_depend d on d.objid = sc.oid and d.deptype = 'a'
        join pg_class t on t.oid = d.refobjid
        join pg_attribute a on a.attrelid = t.oid and a.attnum = d.refobjsubid
        where s.schemaname = 'public'
        """)
    ).all()
    fixed: dict[str, int] = {}
    for s in seqs:
        mx = conn.execute(text(f'select max("{s.col}") from "{s.tbl}"')).scalar()
        if mx is None:  # empty table: setval(seq, NULL) raises
            continue
        if (s.last_value or 0) < mx:
            conn.execute(text("select setval(:s, :v, true)"), {"s": s.sequencename, "v": mx})
            fixed[s.tbl] = mx
    return fixed


#: Cached leaderboards are DERIVED data identified by a NATURAL key (generator x scope x
#: criterion x view), with a surrogate id that carries no meaning of its own. An internal refit
#: reassigns those ids, so merging by primary key inserts a "new" row whose natural key the live
#: database already holds — and the unique constraint rejects it:
#:
#:     UniqueViolation: duplicate key value violates unique constraint "uq_kingdom_judge_scope"
#:
#: That fires on EVERY import after the first; the initial release survived only because it went
#: into an empty database. Nothing holds a foreign key to these tables, so replacing them
#: wholesale is both correct and simpler than teaching merge() about per-table natural keys.
BOARD_CACHE_TABLES = ("rating", "kingdom_rating", "judge_rating", "kingdom_judge_rating")


def replace_board_caches(eng, tables: dict) -> dict:
    """Clear and reload each cached board the bundle actually supplies. Returns {table: rows}.

    Only tables PRESENT in the bundle are cleared. Blanket-clearing would blank a live
    leaderboard whenever a bundle happened to carry no rows for it, turning a partial export into
    a visibly empty board.
    """
    done = {}
    with Session(eng) as s:
        for name in BOARD_CACHE_TABLES:
            model = _BY_TABLE.get(name)
            if model is None or name not in tables:
                continue
            s.query(model).delete()
            s.flush()
            for d in tables[name]:
                s.add(model(**_coerce_datetimes(model, d)))
            done[name] = len(tables[name])
        s.commit()
    return done


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
                if name in BOARD_CACHE_TABLES:
                    continue  # handled below by replace_board_caches (natural-key tables)
                for d in tables.get(name, []):
                    s.merge(model(**_coerce_datetimes(model, d)))  # merge = idempotent by PK
                counts[name] = len(tables.get(name, []))
            s.commit()
        # After the referenced rows (generator, criterion) exist, so the FKs resolve.
        counts.update(replace_board_caches(eng, tables))
        # Must run AFTER the merges land: explicit-id inserts leave every sequence behind, and
        # the next organic insert would collide. Reported so an import that silently fixed a
        # lagging sequence is visible in the output rather than invisible.
        with eng.begin() as conn:
            synced = sync_id_sequences(conn)
        if synced:
            counts["sequences_synced"] = len(synced)

    counts.update(_upload_assets(b, storage))
    return counts


_LOCAL_STORAGE_REFUSAL = """\
refusing to publish a bundle into LOCAL storage.

BIO3D_STORAGE_BACKEND resolved to {backend!r}, so every blob would be written to the local
asset directory and NOTHING would reach the object store. The counts below would still print
green and the public site would keep serving whatever is already in the bucket, so the release
would look like it worked. That is why this refuses instead of warning.

If the release secrets use the R2 names (R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
R2_ACCOUNT_ENDPOINT / R2_BUCKET), nothing translates them into the BIO3D_/AWS_ names this code
reads — see deploy/README.md for the exact mapping and the read-only HEAD pre-flight.

If you genuinely mean a local import (rebuilding a local preview from a bundle), say so with
--local-assets."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument(
        "--assets-only",
        action="store_true",
        help="skip the row import and only sync blobs — for resuming an interrupted upload "
        "without replaying every row against a remote database again",
    )
    ap.add_argument(
        "--local-assets",
        action="store_true",
        help="allow importing into local storage — for rebuilding a local preview from a "
        "bundle. Without it a local backend is refused, because on the release path it is "
        "silently a no-op rather than an error",
    )
    a = ap.parse_args()
    # Ask the backend what it IS, rather than re-deriving it from config: `remote` is the same
    # flag app/main.py and app/seed.py branch on. The check lives here at the CLI boundary and
    # not in import_bundle(), because import_bundle legitimately accepts any storage it is
    # handed (the round-trip tests inject local temp backends) — it is only at the point where
    # the environment silently chose for us that "local" is indistinguishable from a mistake.
    storage = get_storage()
    if not storage.remote and not a.local_assets:
        raise SystemExit(_LOCAL_STORAGE_REFUSAL.format(backend=config.STORAGE_BACKEND))
    counts = import_bundle(
        a.bundle,
        database_url=config.DATABASE_URL,
        storage=storage,
        rows=not a.assets_only,
    )
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
