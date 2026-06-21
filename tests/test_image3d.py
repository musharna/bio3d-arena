import pytest
import trimesh

from app.image3d import Image3DError, generate_tripo


class FakeTransport:
    """Drives generate_tripo's submit->poll->download state machine without network.
    `poll_statuses` is consumed one per poll call (last one repeats)."""

    def __init__(self, poll_statuses, model_url, glb):
        self._statuses = list(poll_statuses)
        self._model_url = model_url
        self._glb = glb
        self.calls = []

    def upload(self, image_bytes, api_key):
        self.calls.append("upload")
        return "file-token-xyz"

    def create_task(self, file_token, api_key):
        self.calls.append("create_task")
        assert file_token == "file-token-xyz"
        return "task-123"

    def poll(self, task_id, api_key):
        self.calls.append("poll")
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        url = self._model_url if status == "success" else None
        return status, url

    def download(self, url):
        self.calls.append("download")
        assert url == self._model_url
        return self._glb


def _box_glb() -> bytes:
    return trimesh.creation.box().export(file_type="glb")


def test_generate_tripo_runs_state_machine_and_returns_glb():
    glb = _box_glb()
    t = FakeTransport(["running", "success"], "https://x/model.glb", glb)
    out = generate_tripo(b"img", api_key="k", transport=t, poll_interval_s=0)
    assert out == glb
    assert t.calls == ["upload", "create_task", "poll", "poll", "download"]


def test_generate_tripo_raises_on_failed_status():
    t = FakeTransport(["failed"], "https://x/model.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_tripo(b"img", api_key="k", transport=t, poll_interval_s=0)


def test_generate_tripo_times_out():
    t = FakeTransport(["running"], "https://x/model.glb", _box_glb())
    with pytest.raises(Image3DError):
        generate_tripo(b"img", api_key="k", transport=t, timeout_s=0, poll_interval_s=0)


def test_generate_tripo_raises_on_empty_download():
    t = FakeTransport(["success"], "https://x/model.glb", b"")
    with pytest.raises(Image3DError):
        generate_tripo(b"img", api_key="k", transport=t, poll_interval_s=0)
