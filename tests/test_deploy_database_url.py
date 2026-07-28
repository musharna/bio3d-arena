"""A managed-Postgres URL pasted verbatim must work.

Found preparing the first real deploy, 2026-07-28. Two faults, one symptom — the container
cannot reach its database:

1. The Dockerfile installs only `requirements.txt`, which carries no Postgres driver and no
   boto3. Both live in `requirements-scale.txt`, which the image never installed. A public
   deploy configured for Postgres + S3 (which is the only supported public configuration —
   see deploy/README.md) therefore booted with neither.

2. Every managed provider — Neon, Supabase, RDS — hands out a URL starting `postgresql://`.
   SQLAlchemy maps that bare scheme to **psycopg2**, while the pinned driver is **psycopg v3**,
   whose dialect is `postgresql+psycopg://`. So the operator pastes the exact string their
   provider gave them and gets an ImportError about a driver they were never told to install.

The second is the one worth fixing properly. Requiring an operator to know SQLAlchemy's dialect
naming in order to use a standard Postgres URL is a trap with no upside: the URL they were
handed is correct, and the code is the thing that should adapt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import database

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://u:p@host/db",
        "postgres://u:p@host/db",  # the older scheme some providers still emit
    ],
)
def test_bare_postgres_scheme_is_pointed_at_the_installed_driver(raw):
    """The whole point: paste what Neon gives you, and it works."""
    out = database.normalize_database_url(raw)
    assert out.startswith("postgresql+psycopg://"), out


def test_query_string_and_credentials_survive_normalisation():
    """Neon appends ?sslmode=require&channel_binding=require, and dropping either would turn a
    TLS-required connection into a confusing refusal."""
    raw = "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require&channel_binding=require"
    out = database.normalize_database_url(raw)
    assert out.endswith("?sslmode=require&channel_binding=require")
    assert "u:p@ep-x.eu-central-1.aws.neon.tech/db" in out


def test_an_explicit_driver_is_left_alone():
    """If someone already knows to say +psycopg, or deliberately wants psycopg2, respect it."""
    for raw in ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg2://u:p@h/d"):
        assert database.normalize_database_url(raw) == raw


def test_sqlite_is_untouched():
    """Local dev and the whole test suite run on SQLite — normalisation must not reach them."""
    for raw in ("sqlite:///./data/arena.db", "sqlite:///:memory:"):
        assert database.normalize_database_url(raw) == raw


def test_the_engine_module_normalises_before_creating_the_engine():
    """Pinning the helper alone would let the engine keep bypassing it, so assert the module
    exposes the normalised URL and that the engine was built from it.

    Deliberately checks the parsed drivername rather than instantiating a live engine:
    `create_engine(...).dialect` imports psycopg, which is in requirements-scale.txt and so is
    absent from both this workstation and CI's dev install. Asserting the dialect NAME tests
    our contract — "the URL now points at psycopg v3" — without making the test depend on a
    driver the public image installs and the test environment does not.
    """
    from sqlalchemy.engine.url import make_url

    assert database.DATABASE_URL == database.normalize_database_url(config_url())
    assert str(database.engine.url).startswith("sqlite")  # the suite itself runs on sqlite

    url = make_url(database.normalize_database_url("postgresql://u:p@h/d"))
    assert url.drivername == "postgresql+psycopg", url.drivername


def config_url() -> str:
    from app import config

    return config.DATABASE_URL


def test_the_image_installs_the_backends_the_public_deploy_needs():
    """deploy/README.md configures the public instance with Postgres + S3. An image that cannot
    import their drivers cannot serve that configuration."""
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "requirements-scale.txt" in dockerfile, (
        "the image must install the scale backends (psycopg, boto3) — the public deploy uses "
        "BIO3D_DATABASE_URL=postgresql://... and BIO3D_STORAGE_BACKEND=s3"
    )
