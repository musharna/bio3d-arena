"""The publish path must not silently degrade to a local write.

`config.STORAGE_BACKEND` defaults to ``"local"`` (app/config.py), and nothing translates the
release secrets' R2 names into the ``AWS_*`` names boto3 reads. An operator who sources the
secret file and runs the import therefore gets a ``LocalStorageBackend``: the blob phase writes
into ``data/assets``, every count prints green, and not one object reaches the bucket. The site
keeps serving whatever was already there, so the release *looks* like it worked. That is the
failure this guards -- a silent no-op on the release path, found during the LOD release and
documented in deploy/README.md as a stopgap (PR #136).

The refusal is only half the contract. Rebuilding the local preview from a bundle is a
legitimate local import, so the escape hatch is asserted here too: a guard that also blocks the
honest case would just get removed by the next operator who hits it.
"""

from __future__ import annotations

import pytest

from app.storage import LocalStorageBackend, StorageBackend
from scripts import import_public


class _RemoteBackend(StorageBackend):
    """Stands in for S3. `remote` is the flag the guard reads, as app/main.py and seed.py do."""

    remote = True

    def save(self, *a, **k):  # pragma: no cover - the guard must not touch storage
        raise AssertionError("storage touched")

    def url_for(self, *a, **k):  # pragma: no cover - the guard must not touch storage
        raise AssertionError("storage touched")

    def read(self, *a, **k):  # pragma: no cover - the guard must not touch storage
        raise AssertionError("storage touched")

    def exists(self, *a, **k):  # pragma: no cover - the guard must not touch storage
        raise AssertionError("storage touched")


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """Record whether main() reached the import, without performing one.

    The damage being guarded against is that the import *runs* and reports success, so the
    assertion that matters is "import_bundle was never entered" -- not merely that something
    raised.
    """
    seen: list[dict] = []
    monkeypatch.setattr(
        import_public, "import_bundle", lambda b, **kw: seen.append({"bundle": b, **kw}) or {}
    )
    monkeypatch.setattr("sys.argv", ["import_public", "--bundle", str(tmp_path / "v9")])
    return seen


def test_refuses_a_local_backend_before_importing_anything(monkeypatch, tmp_path, calls):
    monkeypatch.setattr(import_public, "get_storage", lambda: LocalStorageBackend(tmp_path))

    with pytest.raises(SystemExit) as e:
        import_public.main()

    assert calls == [], "the import ran anyway -- the release would still report success"
    msg = str(e.value)
    # Name the knob, or the operator learns only that it refused, not what to change.
    assert "BIO3D_STORAGE_BACKEND" in msg
    assert "--local-assets" in msg


def test_local_assets_flag_allows_the_deliberate_local_import(monkeypatch, tmp_path, calls):
    """Positive control: the honest local import (preview rebuild) must still work."""
    monkeypatch.setattr(import_public, "get_storage", lambda: LocalStorageBackend(tmp_path))
    monkeypatch.setattr(
        "sys.argv", ["import_public", "--bundle", str(tmp_path / "v9"), "--local-assets"]
    )

    assert import_public.main() == 0
    assert len(calls) == 1


def test_a_remote_backend_needs_no_flag(monkeypatch, calls):
    """Positive control: the guard is specific to local storage, not a blanket refusal."""
    monkeypatch.setattr(import_public, "get_storage", lambda: _RemoteBackend())

    assert import_public.main() == 0
    assert len(calls) == 1
