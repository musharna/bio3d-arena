"""SQLAlchemy engine + session factory. SQLite for the MVP; swap DATABASE_URL for Postgres."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config

logger = logging.getLogger(__name__)

config.ensure_dirs()


def engine_kwargs(url: str) -> dict:
    """create_engine args by dialect.

    SQLite (single-file dev): share the connection across FastAPI's threadpool, and wait up
    to 30s for a write lock (``timeout``→busy_timeout) instead of failing instantly — so a
    batch scorer's brief per-output write never 500s a concurrent arena request.
    Postgres/MySQL (production): a real connection pool with liveness checks so the
    app survives idle-connection drops and many concurrent workers.
    """
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return {
        "pool_pre_ping": True,  # validate connections before use (drop stale ones)
        "pool_size": config.DB_POOL_SIZE,
        "max_overflow": config.DB_MAX_OVERFLOW,
    }


engine = create_engine(config.DATABASE_URL, future=True, **engine_kwargs(config.DATABASE_URL))

if config.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        """WAL so readers never block on the single writer; NORMAL sync is durable enough
        for a dev arena and far faster; busy_timeout backstops the connect-arg timeout."""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


_session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

_schema_ready = False


def _ensure_schema_once() -> None:
    """Self-heal the schema the first time ANY session is opened — app boot, a standalone
    script, or a test — so no entry point has to remember to call init_db(). The recurring,
    money-losing failure this prevents: a generate_* script runs paid image→3D API calls and
    then dies at the DB write because the study DB predated a new column (create_all never adds
    columns to existing tables). Runs init_db() once per process; idempotent. The flag is set
    only AFTER init_db() succeeds so a transient failure retries on the next session."""
    global _schema_ready
    if _schema_ready:
        return
    init_db()
    _schema_ready = True


def SessionLocal(*args, **kwargs) -> Session:
    """Session factory that self-heals the schema on first use (see _ensure_schema_once).
    Callable-compatible drop-in for the underlying sessionmaker; `SessionLocal()` and
    `with SessionLocal() as db:` both work exactly as before."""
    _ensure_schema_once()
    return _session_factory(*args, **kwargs)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns(engine) -> None:  # noqa: ANN001
    """create_all does not add columns to existing tables; diff EVERY model table against the DB
    and ADD any missing additive column so a pre-existing / restored SQLite DB self-heals on boot.

    Model-driven (not a hardcoded list) — the hardcoded version drifted from the models and let
    voter_session.user_id (and hidden_at, generator.paradigm before it) break the app on a stale
    DB. Only columns that can be added safely are healed: a NULLABLE column, or one with a scalar
    Python default (embedded as a literal DEFAULT so a NOT NULL column is fillable). A NOT NULL
    column with only a callable/no default needs a real migration and is logged, not force-added."""
    from . import models  # noqa: F401  (registers tables on Base.metadata)

    with engine.begin() as conn:
        db_tables = {
            r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in Base.metadata.tables.values():
            if table.name not in db_tables:
                continue  # a wholly-missing table is create_all's job, not ours
            have = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(engine.dialect)
                dflt = getattr(col.default, "arg", None)
                if isinstance(dflt, bool):
                    default_sql = f" DEFAULT {1 if dflt else 0}"
                elif isinstance(dflt, (int, float)):
                    default_sql = f" DEFAULT {dflt}"
                elif isinstance(dflt, str):
                    default_sql = f" DEFAULT {dflt!r}"
                elif col.nullable:
                    default_sql = ""
                else:
                    logger.warning(
                        "schema drift: %s.%s is NOT NULL with no scalar default — needs a "
                        "migration, skipping self-heal",
                        table.name,
                        col.name,
                    )
                    continue
                conn.exec_driver_sql(
                    f"ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}{default_sql}"
                )
                logger.info("self-healed missing column %s.%s", table.name, col.name)


def _ensure_commission_attempt_identity(engine) -> None:  # noqa: ANN001
    """Rebuild commission_attempt when it still carries the OLD UNIQUE(model_id, task_id).

    An attempt is a MEASUREMENT of a cell under a named harness, so its identity is
    (model_id, task_id, protocol) — see models.CommissionAttempt. A DB created before that was true
    asserts a cell can be measured once, ever, which forbids the very re-run the protocol change
    exists to run.

    This is the one schema fix _ensure_columns structurally cannot make: it only ADDs columns, and
    SQLite cannot ALTER a constraint at all — changing one means rebuilding the table. So: rename
    the stale table aside, let the ORM's own metadata create the correct one (no hand-written DDL to
    drift from the model), copy every row across, drop the stale one. Lossless by construction — the
    legacy rows are the evidence for why the protocol changed, and a migration that dropped them to
    make room would be deleting the finding.

    Idempotent: a DB whose unique index already spans protocol is left untouched."""
    from . import models  # noqa: F401  (registers the table on Base.metadata)

    name = "commission_attempt"
    table = Base.metadata.tables[name]
    stale_name = f"{name}_stale"
    with engine.begin() as conn:
        tables = {
            r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if name not in tables:
            return  # create_all's job — it will build the correct constraint

        # Every cursor is drained into a list before the next statement runs: DDL against a table
        # whose PRAGMA cursor is still open locks it ("database table is locked").
        indexes = list(conn.exec_driver_sql(f"PRAGMA index_list({name})"))
        stale = any(
            idx[2]  # unique?
            and {r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info({idx[1]})")}
            == {"model_id", "task_id"}
            for idx in indexes
        )
        if not stale:
            return

        have = {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({name})")}
        carried = [c.name for c in table.columns if c.name in have]

        conn.exec_driver_sql(f"ALTER TABLE {name} RENAME TO {stale_name}")
        # SQLite does not rename a table's indexes along with it, so the stale table still owns the
        # names the new table is about to claim (ix_commission_attempt_model_id, ...). Drop the
        # explicitly created ones (origin 'c'); the constraint's own auto-indexes are unnamed and go
        # with the table. The stale table is dropped moments later, so nothing is lost.
        for idx in list(conn.exec_driver_sql(f"PRAGMA index_list({stale_name})")):
            if idx[3] == "c":
                conn.exec_driver_sql(f"DROP INDEX {idx[1]}")

        table.create(bind=conn)
        cols_sql = ", ".join(carried)
        conn.exec_driver_sql(f"INSERT INTO {name} ({cols_sql}) SELECT {cols_sql} FROM {stale_name}")
        conn.exec_driver_sql(f"DROP TABLE {stale_name}")
        logger.info("rebuilt %s: identity is now (model_id, task_id, protocol)", name)


def ensure_schema(target_engine=None) -> None:  # noqa: ANN001
    """Bring a DB up to the current schema: create missing tables, ADD missing additive columns,
    then rebuild any table whose CONSTRAINTS are stale. Idempotent.

    Takes an engine so a tool that writes a DB other than this process's own (scripts that promote
    into the study DB) can heal its target instead of failing on a schema it never booted."""
    from . import models  # noqa: F401  (import registers models on Base.metadata)

    eng = target_engine if target_engine is not None else engine
    Base.metadata.create_all(bind=eng)
    _ensure_columns(eng)
    _ensure_commission_attempt_identity(eng)


def init_db() -> None:
    """Self-heal this process's own DB. Idempotent."""
    ensure_schema(engine)
