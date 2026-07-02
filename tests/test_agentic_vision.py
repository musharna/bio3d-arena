from app.agentic import vision_complete


class _Resp:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}


def test_vision_complete_builds_vision_content_and_returns_text():
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _Resp("IMPROVED SCRIPT")

    out = vision_complete(fake_post, "openai/gpt-5.1", "critique this", b"\x89PNGdata", api_key="k")
    assert out == "IMPROVED SCRIPT"
    content = captured["json"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "critique this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["headers"]["Authorization"] == "Bearer k"  # key in header only
