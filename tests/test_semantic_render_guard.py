"""Unit coverage for the timeout-guarded per-output render in scripts/score_semantic.py.
A wedged model-viewer render must raise (so evaluate_outputs skips the output) rather than hang,
and a cached sheet must be read without spawning a render subprocess."""

import subprocess

import pytest

from scripts import score_semantic as ss


def test_sheet_provider_reads_cached_sheet(monkeypatch):
    monkeypatch.setattr(ss.os.path, "exists", lambda p: True)
    monkeypatch.setattr(ss.os.path, "getsize", lambda p: 123)

    called = {"popen": False}

    def _no_popen(*a, **k):
        called["popen"] = True
        raise AssertionError("must not render when the sheet is cached")

    monkeypatch.setattr(ss.subprocess, "Popen", _no_popen)
    monkeypatch.setattr("builtins.open", lambda *a, **k: _FakeFile(b"PNGBYTES"))

    sheet_for = ss._sheet_provider()
    assert sheet_for(7) == b"PNGBYTES"
    assert called["popen"] is False


def test_sheet_provider_raises_on_render_timeout(monkeypatch):
    monkeypatch.setattr(ss.os.path, "exists", lambda p: False)  # not cached -> must render

    class _WedgedProc:
        pid = 999999

        def wait(self, timeout=None):
            # First call (with a timeout) wedges; the post-kill cleanup wait() returns the code.
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="render", timeout=timeout)
            return -9

    monkeypatch.setattr(ss.subprocess, "Popen", lambda *a, **k: _WedgedProc())
    monkeypatch.setattr(ss.os, "getpgid", lambda pid: pid)
    killed = {"pg": None}
    monkeypatch.setattr(ss.os, "killpg", lambda pg, sig: killed.__setitem__("pg", pg))

    sheet_for = ss._sheet_provider()
    with pytest.raises(RuntimeError, match="wedged"):
        sheet_for(42)
    assert killed["pg"] == 999999  # the process group was force-killed


def test_sheet_provider_raises_on_render_nonzero_exit(monkeypatch):
    monkeypatch.setattr(ss.os.path, "exists", lambda p: False)

    class _FailProc:
        pid = 1

        def wait(self, timeout=None):
            return 1  # non-zero exit

    monkeypatch.setattr(ss.subprocess, "Popen", lambda *a, **k: _FailProc())

    sheet_for = ss._sheet_provider()
    with pytest.raises(RuntimeError, match="render failed"):
        sheet_for(43)


class _FakeFile:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._data
