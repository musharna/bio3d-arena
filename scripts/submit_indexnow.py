#!/usr/bin/env python3
"""Submit this site's sitemap URLs to IndexNow.

One POST reaches Bing, Yandex, Seznam and Naver. Unlike Search Console it needs no account —
the key file on the domain is the proof of ownership — which is why this can live in the repo
and be run from anywhere.

Usage:

    # what would be submitted, no request made
    python scripts/submit_indexnow.py --dry-run

    # for real, against the live site
    python scripts/submit_indexnow.py --base-url https://bio3d-arena.fly.dev

The key must already be served at `<base-url>/indexnow-key.txt` — that is the app reading
BIO3D_INDEXNOW_KEY (see app/indexnow.py), so a deploy without the env var set will be refused
here rather than earning a 403 from the API. Run it after a deploy that adds or changes URLs;
running it when nothing changed is not harmful but wastes everyone's quota.

Deliberately reads the URL list from the LIVE sitemap rather than rebuilding it locally: the
point is to submit what the site actually serves, and a locally-computed list could differ from
the deployed one — which is exactly the class of drift the sitemap work has been fixing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import indexnow  # noqa: E402

TIMEOUT = 30
UA = "bio3d-arena/0.1 (+https://github.com/musharna/bio3d-arena; indexnow submitter)"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")


def sitemap_urls(base_url: str) -> list[str]:
    body = _get(f"{base_url}/sitemap.xml")
    return re.findall(r"<loc>(.*?)</loc>", body)


def remote_key(base_url: str) -> str:
    """The key the LIVE site is serving. Fail loudly if it is not."""
    try:
        return _get(f"{base_url}{indexnow.KEY_PATH}").strip()
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"no key file at {base_url}{indexnow.KEY_PATH} (HTTP {e.code}). The deploy needs "
            "BIO3D_INDEXNOW_KEY set — without it the API would answer 403 and the reason "
            "would be several steps removed from the cause."
        ) from e


def submit(payload: dict, *, dry_run: bool) -> int:
    if dry_run:
        print(json.dumps({**payload, "urlList": payload["urlList"][:5] + ["..."]}, indent=2))
        return 0
    req = urllib.request.Request(
        indexnow.ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            print(f"  HTTP {r.status} — {_MEANING.get(r.status, 'see indexnow.org')}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} — {_MEANING.get(e.code, 'see indexnow.org')}", file=sys.stderr)
        return 1


#: Documented at indexnow.org/documentation (checked 2026-07-30). 202 is a SUCCESS: the
#: submission was accepted and the key is being validated asynchronously.
_MEANING = {
    200: "accepted",
    202: "accepted, key validation pending",
    400: "malformed request",
    403: "key not found or does not match the key file",
    422: "a URL does not belong to the declared host",
    429: "rate limited",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="https://bio3d-arena.fly.dev")
    ap.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    urls = sitemap_urls(base)
    if not urls:
        raise SystemExit(f"no <loc> entries in {base}/sitemap.xml — nothing to submit")
    key = remote_key(base)

    print(f"{len(urls)} URLs from {base}/sitemap.xml, key served at {base}{indexnow.KEY_PATH}")
    rc = 0
    for batch in indexnow.batches(urls):
        print(f"submitting {len(batch)} URLs...")
        rc |= submit(indexnow.build_payload(batch, key=key), dry_run=args.dry_run)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
