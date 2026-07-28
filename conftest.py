"""Pytest bootstrap: isolate the test DB + assets into a temp dir before app import."""

import os
import sys
import shutil
import tempfile
from pathlib import Path


# SAFETY GUARD: refuse to run if the DB has been pointed at a non-throwaway target. The suite
# drops/recreates tables and would WIPE it — this exact mistake destroyed the study DB on
# 2026-06-28 (pytest run with BIO3D_DATABASE_URL=…/arena-study.db). Tests isolate into a temp
# dir below, so these vars should never be set for a test run. NOTE: inlined (not imported from
# app.config) on purpose — importing app here, before the setdefaults below, would freeze config
# at the wrong env values. The same logic is unit-tested via app.config.is_safe_test_db_target.
def _is_safe_test_db_target(value: str | None) -> bool:
    if not value:
        return True
    low = value.lower()
    return any(m in low for m in (":memory:", "/tmp/", "bio3d_test_", "test"))


for _var in ("BIO3D_DATABASE_URL", "BIO3D_DB_PATH"):
    _val = os.environ.get(_var)
    if not _is_safe_test_db_target(_val):
        sys.stderr.write(
            f"\n*** REFUSING TO RUN TESTS: {_var}={_val!r} is a non-throwaway DB.\n"
            "    The suite drops/recreates tables and would WIPE it. Unset it (the suite\n"
            "    isolates into a temp DB automatically) or use a :memory:/tmp/test DB.\n\n"
        )
        raise SystemExit(2)

_tmp = Path(tempfile.mkdtemp(prefix="bio3d_test_"))
os.environ.setdefault("BIO3D_DATA_DIR", str(_tmp))
os.environ.setdefault("BIO3D_ADMIN_TOKEN", "test-token")
os.environ.setdefault("BIO3D_BT_BOOTSTRAP", "20")  # keep bootstrap cheap in tests
# Disable random gold injection by default so non-integrity tests are deterministic;
# test_integrity.py sets config.GOLD_RATE explicitly where it needs gold checks.
os.environ.setdefault("BIO3D_GOLD_RATE", "0")

# Seed reference photos into the isolated test ASSET_DIR so tests that read them (e.g.
# test_generate_multiview_recon) don't have to skip or set BIO3D_DATA_DIR externally.
# Source: real data/assets/reference/ (gitignored); skip gracefully if absent (CI without data).
_real_ref = Path(__file__).resolve().parent / "data" / "assets" / "reference"
_test_ref = Path(os.environ["BIO3D_DATA_DIR"]) / "assets" / "reference"
_test_ref.mkdir(parents=True, exist_ok=True)
for _slug in ("arabidopsis", "pinus", "tomato", "rose", "soybean"):
    for _ext in (".jpg", ".json"):
        _src = _real_ref / f"{_slug}_ref{_ext}"
        _dst = _test_ref / f"{_slug}_ref{_ext}"
        if _src.exists() and not _dst.exists():
            shutil.copy(_src, _dst)


import pytest  # noqa: E402  (after the temp-dir/env bootstrap above, by design)


@pytest.fixture(autouse=True)
def _reset_commission_db(request):
    """Isolate commission tests by resetting the database before each test."""
    # Only reset for commission_ingest tests to ensure they have a clean database
    if "test_commission_ingest" in request.node.nodeid:
        from app.database import Base, engine

        # Dropping every table is whole-schema surgery, not DML — per-statement FK checks are
        # meaningless when the referents are being removed too. It also cannot be ordered
        # safely: task ↔ model_output is a reference cycle and SQLite has no ALTER to break it,
        # so SQLAlchemy cannot sort the DROPs (it says so, as an SAWarning). With enforcement
        # on, the first DROP whose referents still exist fails and leaves the schema half torn
        # down, which then errors every later test in the run.
        #
        # AUTOCOMMIT because `PRAGMA foreign_keys` is a silent no-op inside a transaction, and
        # restore it in a finally: this connection goes back to the pool, and the connect-time
        # listener in app/database.py fires only for NEW connections — so a stale OFF would
        # leak enforcement-off into whatever checks out next.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            try:
                Base.metadata.drop_all(bind=conn)
                Base.metadata.create_all(bind=conn)
            finally:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    yield


@pytest.fixture
def db_session():
    """Per-test isolated ``Session`` on the suite's shared (temp-dir) engine.

    ``app.database.engine`` is a single engine for the whole run (see the temp-dir
    setup above), so a plain ``SessionLocal()`` would let rows from one test leak into
    the next. Instead bind a dedicated ``Session`` to its own connection + outer
    transaction, and roll that transaction back on teardown -- nothing the test wrote
    survives past it. Prefer ``db.flush()`` over ``db.commit()`` in tests using this
    fixture (flush is enough to assign PKs); an explicit ``db.commit()`` would end the
    isolation transaction early and persist rows into later tests.
    """
    from sqlalchemy.orm import sessionmaker

    from app.database import engine, init_db

    init_db()
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
