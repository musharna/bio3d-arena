"""Nine of the thirteen public pages shipped the same meta description.

Measured live on 2026-07-30, `/models`, `/dataset`, `/methodology`, `/coverage`, `/tasks`,
`/submit`, `/terms`, `/privacy` and `/licenses` all rendered `base.html`'s fallback — the site
tagline — because none of them overrides the `description` block. Only `/`, `/arena`,
`/leaderboard` and `/organisms` had their own. (A first pass counted six; running the test
against `_SITEMAP_PATHS` rather than a hand-written list found the other three.)

A duplicated description is not a neutral default. It is the string a search engine quotes
verbatim under the link, so nine pages competed for nine different queries while describing
themselves identically. Having just added 74 URLs to the sitemap, the pages those URLs lead to
should say what they are.

The second half of this file covers `schema.org/Dataset` on `/dataset`. The organism pages
deliberately do NOT claim it — they distribute nothing (see tests/test_organism_pages.py).
`/dataset` is the page where the claim is true: it describes "a citable, licensed release of
the biological-3D generation benchmark: tasks, 3D outputs, baked GT reference renders, and
objective metrics". Declaring it there is honest, and it is what Google Dataset Search reads.
"""

from __future__ import annotations

import json
import re

import pytest
from starlette.testclient import TestClient

from app import config
from app.main import _SITEMAP_PATHS, app


@pytest.fixture
def client():
    return TestClient(app)


def description_of(client, path: str) -> str:
    """The rendered <meta name="description"> for a page.

    `re.S` matters: base.html puts the attribute and its content on separate lines, and a
    single-line pattern silently reports every page as having no description at all — which is
    exactly the false reading that nearly sent this work down the wrong path.
    """
    body = client.get(path).text
    m = re.search(r'name="description"\s*content="(.*?)"', body, re.S)
    return m.group(1).strip() if m else ""


# --- descriptions --------------------------------------------------------------------


def test_every_public_page_has_a_description(client):
    missing = [p for p in _SITEMAP_PATHS if not description_of(client, p)]
    assert missing == [], f"pages with no meta description: {missing}"


def test_no_public_page_falls_back_to_the_site_tagline(client):
    """The fallback is correct as a floor and wrong as an answer: it describes the site, so a
    page using it tells a searcher nothing about the page they are about to open."""
    generic = [p for p in _SITEMAP_PATHS if description_of(client, p) == config.SITE_TAGLINE]
    assert generic == [], f"pages still describing themselves with the site tagline: {generic}"


def test_no_two_public_pages_share_a_description(client):
    seen: dict[str, str] = {}
    dupes: list[tuple[str, str]] = []
    for path in _SITEMAP_PATHS:
        d = description_of(client, path)
        if d in seen:
            dupes.append((seen[d], path))
        seen[d] = path
    assert dupes == [], f"pages sharing one description: {dupes}"


def test_no_description_contains_a_raw_newline(client):
    r"""`home.html` and `arena.html` wrapped their description across source lines, so the
    rendered `content="…"` carried literal newlines — visible in the page source as
    `Which AI model best\nrebuilds life in 3D?`.

    HTML attribute values normalise whitespace, so most scrapers recover; the ones that
    reproduce the attribute verbatim (feed readers, some chat unfurlers, anything extracting
    with a plain regex) do not. It costs nothing to emit the string a consumer is meant to read.
    """
    broken = [p for p in _SITEMAP_PATHS if "\n" in description_of(client, p)]
    assert broken == [], f"descriptions containing a raw newline: {broken}"


def test_descriptions_are_a_sensible_length_for_a_search_result(client):
    """Search engines truncate around 155–160 characters. Well past that is text nobody reads;
    far under it wastes the one line of copy that is quoted verbatim."""
    bad = []
    for path in _SITEMAP_PATHS:
        n = len(description_of(client, path))
        if not (60 <= n <= 200):
            bad.append((path, n))
    assert bad == [], f"descriptions outside 60-200 chars: {bad}"


# --- structured data on the page that earns it ----------------------------------------


def _json_ld(client, path: str) -> list[dict]:
    body = client.get(path).text
    return [
        json.loads(b)
        for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    ]


def test_dataset_page_declares_itself_a_dataset(client):
    kinds = [b.get("@type") for b in _json_ld(client, "/dataset")]
    assert "Dataset" in kinds, f"/dataset emits no Dataset markup (found {kinds})"


def test_the_dataset_markup_carries_googles_two_required_properties(client):
    """Google requires exactly two properties on a Dataset: `name` and `description`.

    Checked against developers.google.com/search/docs/appearance/structured-data/dataset on
    2026-07-30, not from recall. An earlier draft of this test asserted that `license` and
    `url` were required too, and that markup missing them "is ingested as incomplete and can
    be dropped entirely" — wrong on both counts. Everything beyond name and description is
    listed as RECOMMENDED, and Google does not say a dataset lacking them is ineligible.
    """
    block = next(b for b in _json_ld(client, "/dataset") if b.get("@type") == "Dataset")
    for field in ("name", "description"):
        assert block.get(field), f"Dataset markup missing required field {field!r}"


def test_the_dataset_markup_also_carries_the_recommended_fields_we_can_state_truthfully(client):
    """`license`, `url` and `keywords` are recommended, not required, and this project can
    state all three honestly — so it does. `license` is a URL to the licences page rather than
    an SPDX identifier because a release's terms are a per-item rollup with no single id."""
    block = next(b for b in _json_ld(client, "/dataset") if b.get("@type") == "Dataset")
    for field in ("license", "url", "keywords"):
        assert block.get(field), f"Dataset markup missing recommended field {field!r}"
    assert block["url"].startswith(config.PUBLIC_BASE_URL), "Dataset url must be absolute"


def test_the_dataset_markup_does_not_promise_a_download_that_is_not_there(client):
    """`distribution` asserts a retrievable file. The public instance publishes releases only
    when one has been cut, so the key must be present exactly when a release is, and absent
    otherwise — a dead `contentUrl` is worse markup than none.

    Its absence costs nothing in eligibility: `distribution` is recommended, not required, and
    Google does not state that a dataset without one is excluded from Dataset Search. Cutting a
    release would improve discoverability; it is not a precondition for being indexed.
    """
    block = next(b for b in _json_ld(client, "/dataset") if b.get("@type") == "Dataset")
    for dist in block.get("distribution", []):
        assert dist.get("contentUrl"), "a distribution entry with no contentUrl"
        assert client.get(dist["contentUrl"].replace(config.PUBLIC_BASE_URL, "")).status_code != 404


def test_only_the_dataset_page_claims_to_be_a_dataset(client):
    """Guards the line drawn in tests/test_organism_pages.py: the claim belongs to the page
    that actually distributes something, and spreading it across the site to farm rich results
    is the dishonest version of this change."""
    for path in _SITEMAP_PATHS:
        if path == "/dataset":
            continue
        kinds = [b.get("@type") for b in _json_ld(client, path)]
        assert "Dataset" not in kinds, f"{path} claims to be a Dataset"


# --- llms.txt -------------------------------------------------------------------------


def test_llms_txt_is_served_as_plain_text(client):
    r = client.get("/llms.txt")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]


def test_llms_txt_describes_the_site_and_points_at_its_real_surfaces(client):
    """The convention is a short factual map of the site for a model reading it directly,
    rather than the rendered HTML. Worth having because a growing share of arrivals come from
    someone asking a model rather than a search engine."""
    body = client.get("/llms.txt").text
    assert body.startswith("# "), "llms.txt should open with a markdown H1"
    for path in ("/arena", "/leaderboard", "/organisms", "/methodology", "/dataset"):
        assert path in body, f"llms.txt does not mention {path}"


def test_llms_txt_does_not_advertise_an_internal_page(client):
    """Same rule the sitemap follows: internal research surfaces 404 publicly, so naming them
    here would describe a site that does not exist."""
    body = client.get("/llms.txt").text
    for path in ("/benchmark", "/research", "/spotlight", "/admin"):
        assert path not in body, f"llms.txt advertises internal page {path}"
