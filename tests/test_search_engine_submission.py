"""The sitemap could only be found by accident.

After a day of indexing work — 12 URLs to 86, sixteen new organism pages, per-page descriptions
— the site still had no search-engine verification and no analytics of any kind. `robots.txt`
advertises the sitemap, so a crawler that already knows about the site will eventually read it,
but nothing tells a search engine the site exists, nothing submits the sitemap directly, and
nothing reports back whether any of it worked. Every further choice would have been another
unmeasured guess on a pile of unmeasured guesses.

Two halves here, split by what can be automated:

**Verification tags** need a token issued by Google/Bing against the operator's own account, so
they are config, rendered only when set. Absent config renders no tag at all rather than an
empty one — a `content=""` verification tag is a broken claim of ownership, not a neutral
default.

**IndexNow** needs no account at all: publish a key file, POST the changed URLs, and Bing,
Yandex, Seznam and Naver all receive it. That half is fully ours, so it is built here rather
than handed over.

Protocol details below are from indexnow.org/documentation, checked 2026-07-30, not recalled:
key is 8–128 chars of [a-zA-Z0-9-]; the POST body carries `host`, `key`, `keyLocation` and
`urlList`; at most 10,000 URLs per request; 200 = accepted, 202 = accepted pending key
validation, 403 = key not found, 422 = a URL does not belong to the host.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import config, indexnow
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(config, "INDEXNOW_KEY", "abc123def456-0000")
    return "abc123def456-0000"


# --- the key file --------------------------------------------------------------------


def test_the_key_file_serves_the_key_as_plain_text(client, keyed):
    r = client.get(indexnow.KEY_PATH)
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert r.text.strip() == keyed, "the file's whole content must be the key"


def test_the_key_file_404s_when_no_key_is_configured(client, monkeypatch):
    """An instance that has not been given a key must not serve an empty file — a key file
    containing nothing verifies nothing, and IndexNow answers 403 for it."""
    monkeypatch.setattr(config, "INDEXNOW_KEY", "")
    assert client.get(indexnow.KEY_PATH).status_code == 404


def test_the_key_path_does_not_shadow_the_other_text_files(client, keyed):
    """The spec's default is `/{key}.txt`, which as a route would be a catch-all on every
    `.txt` path — and this site already serves `/robots.txt` and `/llms.txt`. The spec's second
    option (a fixed path, declared to the API as `keyLocation`) is used instead, so no existing
    or future `.txt` route can be swallowed."""
    assert client.get("/robots.txt").status_code == 200
    assert client.get("/llms.txt").status_code == 200
    assert indexnow.KEY_PATH not in ("/robots.txt", "/llms.txt")


def test_a_configured_key_is_valid_under_the_protocol():
    """8–128 characters of [a-zA-Z0-9-]. A key outside that is rejected by the API, and the
    failure surfaces as a 403 on submission rather than at boot, which is far from the cause."""
    key = indexnow.generate_key()
    assert 8 <= len(key) <= 128
    assert all(c.isalnum() or c == "-" for c in key)


# --- the payload ---------------------------------------------------------------------


def test_the_payload_carries_the_four_documented_fields(keyed):
    body = indexnow.build_payload(["https://example.test/a", "https://example.test/b"], key=keyed)
    assert set(body) == {"host", "key", "keyLocation", "urlList"}
    assert body["key"] == keyed
    assert body["keyLocation"].endswith(indexnow.KEY_PATH)


def test_the_payload_host_matches_the_urls_it_submits(keyed):
    """422 is what the API returns when a URL does not belong to the declared host, so the
    host is derived from the URLs rather than configured separately and allowed to drift."""
    body = indexnow.build_payload(["https://bio3d.test/a"], key=keyed)
    assert body["host"] == "bio3d.test"


def test_submitting_a_url_from_another_host_is_refused_before_the_request(keyed):
    """Better to fail here than to spend a request discovering it: a 422 costs a round trip
    and, repeated, looks like the spam the 429 exists to catch."""
    with pytest.raises(ValueError, match="host"):
        indexnow.build_payload(
            ["https://bio3d.test/a", "https://elsewhere.test/b"],
            key=keyed,
        )


def test_the_batch_is_capped_at_the_documented_maximum(keyed):
    """10,000 URLs per POST. The sitemap is far short of that today; the cap is here so the
    day it is not, the request is split rather than silently rejected as malformed."""
    urls = [f"https://bio3d.test/{i}" for i in range(25_000)]
    batches = list(indexnow.batches(urls))
    assert all(len(b) <= indexnow.MAX_URLS_PER_REQUEST for b in batches)
    assert sum(len(b) for b in batches) == len(urls), "every URL must be submitted exactly once"


def test_no_payload_is_built_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "INDEXNOW_KEY", "")
    with pytest.raises(ValueError, match="key"):
        indexnow.build_payload(["https://bio3d.test/a"])


# --- verification tags ---------------------------------------------------------------


def test_the_verification_tags_render_only_when_configured(client, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SITE_VERIFICATION", "g-token-123")
    monkeypatch.setattr(config, "BING_SITE_VERIFICATION", "b-token-456")
    body = client.get("/").text
    assert 'name="google-site-verification"' in body
    assert "g-token-123" in body
    assert 'name="msvalidate.01"' in body
    assert "b-token-456" in body


def test_no_empty_verification_tag_is_emitted(client, monkeypatch):
    """An unconfigured instance must render nothing. `content=""` is a malformed ownership
    claim, and every dev and preview instance would otherwise ship one."""
    monkeypatch.setattr(config, "GOOGLE_SITE_VERIFICATION", "")
    monkeypatch.setattr(config, "BING_SITE_VERIFICATION", "")
    body = client.get("/").text
    assert "google-site-verification" not in body
    assert "msvalidate.01" not in body
