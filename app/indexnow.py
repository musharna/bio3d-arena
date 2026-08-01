"""IndexNow: tell search engines a URL changed instead of waiting to be crawled.

One POST reaches Bing, Yandex, Seznam and Naver at once, and unlike Search Console it needs no
account — publish a key file on the domain, and the key proves ownership. That makes it the
half of search-engine submission that can live in the repository rather than in someone's
browser session.

Protocol per indexnow.org/documentation, checked 2026-07-30 (not recalled):

  * key: 8-128 characters of [a-zA-Z0-9-]
  * key file: UTF-8, contains only the key. The spec's default is `/{key}.txt` at the root;
    hosting it elsewhere is explicitly allowed provided the location is declared as
    `keyLocation`, and then only URLs under that path prefix may be submitted.
  * POST body: {"host", "key", "keyLocation", "urlList"}, at most 10,000 URLs
  * 200 accepted · 202 accepted, key validation pending · 400 malformed · 403 key not found
    · 422 URL does not belong to host · 429 rate limited

This uses the fixed-path option deliberately. A route for `/{key}.txt` is a catch-all over
every `.txt` path, and this site already serves `/robots.txt` and `/llms.txt`; one of those
would be shadowed the moment the key changed, or a future `.txt` file would be. A fixed path at
the domain root has prefix `/`, so every URL on the site remains submittable.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from urllib.parse import urlsplit

from . import config

#: Where the key file is served. Declared to the API as `keyLocation` on every request.
KEY_PATH = "/indexnow-key.txt"

#: Documented ceiling for a single POST.
MAX_URLS_PER_REQUEST = 10_000

ENDPOINT = "https://api.indexnow.org/indexnow"


def generate_key() -> str:
    """A fresh key in the documented character set. 32 hex chars sits mid-range of 8-128."""
    return secrets.token_hex(16)


def batches(urls: list[str]) -> Iterator[list[str]]:
    """Split a URL list into requests the API will accept.

    The sitemap is nowhere near the ceiling today. This exists so that the day it is, the
    submission splits rather than being refused whole as malformed — the failure mode of a
    silently-truncated batch being that the untruncated half is never indexed and nobody
    notices, which is the same shape as the sitemap defect this all started with.
    """
    for i in range(0, len(urls), MAX_URLS_PER_REQUEST):
        yield urls[i : i + MAX_URLS_PER_REQUEST]


def build_payload(urls: list[str], key: str | None = None) -> dict:
    """The POST body for one batch, or raise before spending a request on a rejection.

    `host` is derived from the URLs rather than configured, because the API answers 422 when
    they disagree and a separately-configured host is free to drift from the URLs it describes.
    A mixed-host list is refused here for the same reason: it costs a round trip to be told,
    and repeated, it looks like the abuse the 429 exists to stop.
    """
    key = key if key is not None else config.INDEXNOW_KEY
    if not key:
        raise ValueError(
            "no IndexNow key configured - set BIO3D_INDEXNOW_KEY (see app/indexnow.py)"
        )
    if not urls:
        raise ValueError("no URLs to submit")

    hosts = {urlsplit(u).netloc for u in urls}
    if len(hosts) != 1:
        raise ValueError(f"every URL in one submission must share a host, got {sorted(hosts)}")
    host = hosts.pop()

    return {
        "host": host,
        "key": key,
        "keyLocation": f"{urlsplit(urls[0]).scheme}://{host}{KEY_PATH}",
        "urlList": urls,
    }
