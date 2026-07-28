"""A multi-GB bundle upload must survive a flaky link.

The first real release upload died ~40 minutes in on a single corrupted TLS record
(`SSLV3_ALERT_BAD_RECORD_MAC` from R2). Nothing was wrong with the bundle or the credentials —
one packet went bad on a home connection. The cost was disproportionate: the whole transfer
was all-or-nothing, and the row import was welded to it, so recovering meant re-uploading
every object that had already landed AND replaying every row against a remote Postgres.

Two properties fix that, and both are asserted here: already-present blobs are skipped, and a
transient failure is retried rather than fatal.
"""

from __future__ import annotations

import json

import pytest

from scripts import import_public


class FakeStorage:
    """Minimal StorageBackend stand-in that can be told to fail a given key N times."""

    remote = True

    def __init__(self, fail_times: dict[str, int] | None = None):
        self.objects: dict[str, bytes] = {}
        self.fail_times = dict(fail_times or {})
        self.put_calls: list[str] = []

    def save(self, rel, data):
        self.put_calls.append(rel)
        if self.fail_times.get(rel, 0) > 0:
            self.fail_times[rel] -= 1
            raise OSError(f"simulated transport fault on {rel}")
        self.objects[rel] = data

    def exists(self, rel):
        return rel in self.objects

    def read(self, rel):
        return self.objects[rel]

    def url_for(self, rel):
        return f"/fake/{rel}"


def _bundle(tmp_path, n=3):
    b = tmp_path / "bundle"
    (b / "assets" / "sub").mkdir(parents=True)
    (b / "gt").mkdir(parents=True)
    for i in range(n):
        (b / "assets" / "sub" / f"o{i}.glb").write_bytes(b"glb" + str(i).encode())
    (b / "gt" / "species.glb").write_bytes(b"gtglb")
    rows = json.dumps({}, indent=0, sort_keys=True).encode()
    (b / "rows.json").write_bytes(rows)
    import hashlib

    (b / "manifest.json").write_text(json.dumps({"sha256": hashlib.sha256(rows).hexdigest()}))
    return b


def test_a_resumed_upload_skips_what_is_already_there(tmp_path):
    b = _bundle(tmp_path)
    st = FakeStorage()

    first = import_public.import_bundle(b, database_url="sqlite://", storage=st, rows=False)
    assert first == {"uploaded": 4, "already_present": 0}

    st.put_calls.clear()
    second = import_public.import_bundle(b, database_url="sqlite://", storage=st, rows=False)
    assert second == {"uploaded": 0, "already_present": 4}
    assert st.put_calls == [], "a resumed run must not re-PUT objects that already landed"


def test_a_transient_failure_is_retried_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(import_public.time, "sleep", lambda _s: None)  # no real backoff in tests
    b = _bundle(tmp_path)
    st = FakeStorage(fail_times={"sub/o1.glb": 2})

    counts = import_public.import_bundle(b, database_url="sqlite://", storage=st, rows=False)

    assert counts == {"uploaded": 4, "already_present": 0}
    assert st.put_calls.count("sub/o1.glb") == 3  # two failures then success
    assert st.objects["sub/o1.glb"] == b"glb1"


def test_a_persistent_failure_still_raises(tmp_path, monkeypatch):
    """Retrying must not turn a real, permanent fault (bad credentials, missing bucket) into
    silent success — the run has to end loud."""
    monkeypatch.setattr(import_public.time, "sleep", lambda _s: None)
    b = _bundle(tmp_path)
    st = FakeStorage(fail_times={"sub/o0.glb": 99})

    with pytest.raises(OSError, match="simulated transport fault"):
        import_public.import_bundle(b, database_url="sqlite://", storage=st, rows=False)


def test_assets_only_does_not_touch_the_database(tmp_path):
    """The resume path must not replay rows: pointing it at an unusable database URL and still
    succeeding proves the row phase is genuinely skipped rather than merely fast."""
    b = _bundle(tmp_path)
    st = FakeStorage()

    counts = import_public.import_bundle(
        b, database_url="postgresql+psycopg://nobody@127.0.0.1:1/nope", storage=st, rows=False
    )
    assert counts["uploaded"] == 4
