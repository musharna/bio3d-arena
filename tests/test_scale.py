"""Tests for scale-out seams: storage backend, DB engine pooling, rate limiter."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app import config, integrity, storage
from app.database import engine_kwargs


def test_content_type_mapping():
    assert storage.content_type_for("a/b.glb") == "model/gltf-binary"
    assert storage.content_type_for("x.pdb") == "chemical/x-pdb"
    assert storage.content_type_for("x.cif") == "chemical/x-cif"
    assert storage.content_type_for("x.unknown") == "application/octet-stream"


def test_local_storage_roundtrip():
    root = Path(tempfile.mkdtemp(prefix="bio3d_store_"))
    backend = storage.LocalStorageBackend(root, url_prefix="/assets")
    backend.save("sub/dir/a.glb", b"hello-bytes")
    assert (root / "sub/dir/a.glb").read_bytes() == b"hello-bytes"
    assert backend.read("sub/dir/a.glb") == b"hello-bytes"
    assert backend.url_for("sub/dir/a.glb") == "/assets/sub/dir/a.glb"
    assert backend.remote is False


def test_default_backend_is_local():
    # Default config selects the local filesystem backend (no remote deps).
    assert config.STORAGE_BACKEND == "local"
    assert isinstance(storage.get_storage(), storage.LocalStorageBackend)


def test_engine_kwargs_sqlite_vs_postgres():
    sqlite = engine_kwargs("sqlite:////tmp/x.db")
    assert sqlite["connect_args"]["check_same_thread"] is False
    assert "pool_pre_ping" not in sqlite

    pg = engine_kwargs("postgresql+psycopg://u:p@host/db")
    assert pg["pool_pre_ping"] is True
    assert pg["pool_size"] == config.DB_POOL_SIZE
    assert pg["max_overflow"] == config.DB_MAX_OVERFLOW


def test_in_memory_rate_limiter(monkeypatch):
    monkeypatch.setattr(config, "VOTE_RATE_LIMIT", 2)
    monkeypatch.setattr(config, "VOTE_RATE_WINDOW", 60.0)
    lim = integrity.InMemoryRateLimiter()
    assert lim.allow("s1") is True
    assert lim.allow("s1") is True
    assert lim.allow("s1") is False  # third within window blocked
    assert lim.allow("s2") is True  # a different session is independent
    lim.reset()
    assert lim.allow("s1") is True


def test_limiter_selection_default_in_memory():
    assert config.REDIS_URL == ""
    integrity._limiter.cache_clear()
    assert isinstance(integrity._limiter(), integrity.InMemoryRateLimiter)
