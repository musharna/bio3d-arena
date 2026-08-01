"""Which POOL a reference photo is drawn from, as distinct from whether the taxon resolved
(tests/test_gallery_taxon_resolution.py). Both remaining gutted galleries failed here rather
than at resolution, and for different reasons — measured 2026-07-29 against the gate:

  danaus_plexippus  4/14 — 6 of 10 failures are a chrysalis or a caterpillar. Task 30 asks for
                    "a whole monarch butterfly", so a larva is never a usable reference, yet
                    iNaturalist's pool is full of them (9,259 CC research-grade larval records)
                    because a caterpillar on milkweed is what people photograph.
  rosa              0/8 — 4 are *Rubus* (bramble) left over from the resolver bug, and 4 are
                    genuine *Rosa* in the wrong form (thorny stem only, a spent wild bloom).
                    Unfixable from iNaturalist: 2,446 wild CC records vs ONE cultivated, while
                    the corpus's own task-19 input photo is a cultivated pink garden rose shrub.

Both fixes change the pool rather than the gate: the gate was right to reject these.
"""

from __future__ import annotations

import urllib.parse

import pytest

from app.licensing import REDISTRIBUTABLE_LICENSES
from scripts import source_reference_gallery as srg


def _capture(monkeypatch, results=()):
    """Stub `_get` and record the URL, so the outbound query itself is assertable."""
    seen: dict = {}

    def _get(url):
        seen["url"] = url
        return {"results": list(results)}

    monkeypatch.setattr(srg, "_get", _get)
    return seen


# --- life stage ---------------------------------------------------------------------------


def test_a_metamorphic_taxon_asks_only_for_adults(monkeypatch):
    """iNaturalist's Life Stage annotation is term_id=1; value 2 is Adult. Without it the
    monarch pool is 39,201 records of which 9,259 are larvae."""
    seen = _capture(monkeypatch)
    srg._cc_photos_from_observations(48662, 8, adult_only=True)
    assert "term_id=1" in seen["url"]
    assert "term_value_id=2" in seen["url"]


def test_a_taxon_with_no_life_stages_sends_no_life_stage_filter(monkeypatch):
    """Positive control. The annotation is meaningless for a pine or a mushroom, and iNaturalist
    answers an inapplicable annotation with an empty set — so a filter that leaked onto every
    taxon would empty every gallery rather than fail visibly."""
    seen = _capture(monkeypatch)
    srg._cc_photos_from_observations(55779, 8)
    assert "term_id" not in seen["url"]


def test_the_adult_only_pool_bypasses_the_curated_taxon_photos(monkeypatch):
    """`taxon_photos` is a curated set that CANNOT be annotation-filtered, and for the monarch it
    is exactly where the chrysalis shots live. Same structural reason the cultivated path skips
    it: topping up from a filtered pool still lets the unfiltered photos fill the gallery first."""
    calls: list[str] = []

    def _get(url):
        calls.append(url)
        return {"results": []}

    monkeypatch.setattr(srg, "_get", _get)
    srg._cc_photos(48662, 8, adult_only=True)
    assert not any("/taxa/48662" in u for u in calls), "must not read the unfilterable curated set"
    assert any("observations?" in u for u in calls)


def test_source_taxon_routes_the_monarch_to_the_adult_only_pool(monkeypatch):
    """The wiring, not just the helper: it is the roster lookup in `source_taxon` that decides."""
    seen: dict = {}

    def _cc_photos(tid, n, *, cultivated=False, adult_only=False):
        seen["adult_only"] = adult_only
        return []

    monkeypatch.setattr(srg, "_resolve_taxon_id", lambda b: 48662)
    monkeypatch.setattr(srg, "_cc_photos", _cc_photos)
    srg.source_taxon("Danaus plexippus", 8, True)
    assert seen["adult_only"] is True


def test_the_metamorphic_roster_holds_only_taxa_whose_juveniles_look_different():
    """A dog or a mallard has no life stage that is a different organism to look at, so routing
    them here would only shrink their pool for nothing."""
    assert "Danaus plexippus" in srg.ADULT_ONLY
    assert "Canis lupus familiaris" not in srg.ADULT_ONLY
    assert "Pinus sylvestris" not in srg.ADULT_ONLY


# --- Wikimedia Commons -------------------------------------------------------------------


def test_the_gallery_licence_set_can_never_admit_what_the_allowlist_rejects():
    """`app.licensing.REDISTRIBUTABLE_LICENSES` is documented as the single answer to 'may we
    redistribute this?', and warns that hand-maintained copies have already drifted in BOTH
    directions. This file used to carry a third one. Deriving the gallery's set from it can only
    ever narrow it; this test is what keeps that binding instead of a comment."""
    assert srg.GALLERY_LICENSES <= REDISTRIBUTABLE_LICENSES


def test_the_inaturalist_licence_codes_are_decided_by_the_one_allowlist():
    """Same drift guard on the other source: every iNaturalist code we send must map onto an SPDX
    id the allowlist admits, so widening or narrowing the allowlist moves both sources together."""
    sent = set(srg._INAT_PHOTO_LICENSE.split(","))
    assert sent == {c for c, s in srg._INAT_LICENCE.items() if s in REDISTRIBUTABLE_LICENSES}
    assert "cc-by-sa" in sent, "share-alike is in the allowlist and is Commons' default licence"
    assert all(srg._INAT_LICENCE[c] in REDISTRIBUTABLE_LICENSES for c in sent)


@pytest.mark.parametrize(
    "label,spdx",
    [("CC0", "CC0-1.0"), ("CC BY 4.0", "CC-BY-4.0"), ("CC BY-SA 4.0", "CC-BY-SA-4.0")],
)
def test_commons_licence_labels_map_onto_the_gallery_set(label, spdx):
    assert srg._commons_licence(label) == spdx
    assert spdx in srg.GALLERY_LICENSES


@pytest.mark.parametrize("label", ["CC BY-NC 2.0", "CC BY-ND 4.0", "No restrictions", "", None])
def test_commons_photos_we_must_not_use_are_dropped(label):
    """NC and ND are bars in law. 'No restrictions' is Commons' label for provenance that is merely
    unchallenged — it names no licence we could show a user."""
    assert srg._commons_row(_page(label)) is None


def test_public_domain_commons_files_are_skipped_because_they_are_digitised_print():
    """Not a licence judgement — PD is squarely in the allowlist. A content one, measured: a rose
    search returned 15 of 16 PD hits as pre-1930 seed-catalogue pages and monochrome plates, one
    advertising Sweet William rather than a rose. They are scans of print, not photographs of a
    specimen, so they cannot anchor a fidelity judgement."""
    assert "PUBLIC-DOMAIN" in REDISTRIBUTABLE_LICENSES
    assert "PUBLIC-DOMAIN" not in srg.GALLERY_LICENSES
    assert srg._commons_row(_page("Public domain")) is None


def _page(
    licence, *, artist="<a href='/wiki/User:X'>Jane Doe</a>", url="https://x/y.jpg", pid=12345
):
    em = {}
    if licence is not None:
        em["LicenseShortName"] = {"value": licence}
    return {
        "title": "File:Pink garden rose.jpg",
        "pageid": pid,
        "imageinfo": [
            {
                "thumburl": url,
                "url": "https://x/full.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Pink_garden_rose.jpg",
                "extmetadata": {**em, "Artist": {"value": artist}},
            }
        ],
    }


def test_an_admissible_commons_photo_becomes_a_manifest_row():
    row = srg._commons_row(_page("CC BY 4.0"))
    assert row is not None
    assert row["license"] == "CC-BY-4.0"
    assert row["url"] == "https://x/y.jpg"
    assert row["source"] == "wikimedia-commons"


def test_the_attribution_names_the_author_the_licence_and_commons():
    """CC-BY is only satisfied if the credit line actually reaches the voter — the gallery UI
    renders this string verbatim as the photo credit."""
    row = srg._commons_row(_page("CC BY 4.0"))
    assert "Jane Doe" in row["attribution"]
    assert "CC-BY-4.0" in row["attribution"]
    assert "Wikimedia Commons" in row["attribution"]


def test_the_artist_html_commons_returns_is_reduced_to_a_name():
    """Commons puts a full anchor tag in `Artist`; unescaped it would render as markup in the
    credit line."""
    row = srg._commons_row(_page("CC0", artist='<a href="/wiki/User:Foo" title="t">Ada L</a>'))
    assert row["attribution"].startswith("Ada L")
    assert "<" not in row["attribution"]


def test_commons_sourcing_asks_for_files_and_their_licence_metadata(monkeypatch):
    seen: dict = {}

    def _get(url):
        seen["url"] = url
        return {"query": {"pages": {}}}

    monkeypatch.setattr(srg, "_get", _get)
    srg._commons_photos("garden rose in bloom", 8)
    assert "commons.wikimedia.org" in seen["url"]
    assert "extmetadata" in seen["url"], "the licence must come back with the file, not per-file"
    assert "gsrnamespace=6" in seen["url"], "namespace 6 is File: — otherwise this returns articles"


def test_commons_asks_for_at_most_fifty_files_per_request():
    """A trap that fails SILENTLY and in the safe-looking direction. MediaWiki resolves
    `prop=imageinfo` for at most 50 titles per request, so a larger gsrlimit returns the extra
    pages with NO imageinfo — every licence reads as unknown and every photo is dropped. Asking
    for 200 returned 200 hits and zero admissible photos, which looks exactly like 'Commons has
    nothing' rather than like a bug."""
    seen: dict = {}

    def _get(url):
        seen["url"] = url
        return {"query": {"pages": {}}}

    import scripts.source_reference_gallery as m

    orig = m._get
    m._get = _get
    try:
        m._commons_photos("garden rose", 40)
    finally:
        m._get = orig
    limit = int(urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)["gsrlimit"][0])
    assert limit <= 50


def test_commons_pages_through_results_until_it_has_enough(monkeypatch):
    """One 50-file page yields only a handful of admissible photos (measured: ~8 per 60 hits for
    roses), so without continuation a 16-photo gallery can never fill."""
    page1 = {
        "query": {"pages": {"1": _page("CC0", url="https://x/1.jpg", pid=1)}},
        "continue": {"gsroffset": 50, "continue": "gsroffset||"},
    }
    page2 = {"query": {"pages": {"2": _page("CC BY 4.0", url="https://x/2.jpg", pid=2)}}}
    calls: list[str] = []

    def _get(url):
        calls.append(url)
        return page1 if len(calls) == 1 else page2

    monkeypatch.setattr(srg, "_get", _get)
    rows = srg._commons_photos("garden rose", 2)
    assert len(rows) == 2
    assert "gsroffset=50" in calls[1], "the second request must carry the continuation"


def test_commons_stops_when_there_is_no_continuation(monkeypatch):
    """Positive control on the loop bound: an API that stops offering `continue` must end the
    walk, not spin."""
    calls: list[str] = []

    def _get(url):
        calls.append(url)
        return {"query": {"pages": {"1": _page("CC0", pid=1)}}}

    monkeypatch.setattr(srg, "_get", _get)
    rows = srg._commons_photos("garden rose", 30)
    assert len(rows) == 1
    assert len(calls) == 1


def test_scanned_catalogue_collections_are_dropped_whatever_their_licence():
    """The public-domain proxy was wrong. Six of eight rose photos came back CC-BY-2.0 and were
    still scans of Victorian seed catalogues — the USDA 'Henry G. Gilbert Nursery and Seed Trade
    Catalog Collection', bulk-uploaded under a modern CC licence by the scanning institution. The
    licence says nothing about whether the file is a photograph of a specimen."""
    page = _page(
        "CC BY 2.0",
        artist="A. Currie &amp; Company.; Henry G. Gilbert Nursery and "
        "Seed Trade Catalog Collection",
    )
    assert srg._commons_row(page) is None


def test_an_ordinary_photographer_credit_is_not_mistaken_for_a_catalogue():
    """Positive control on the blocklist: it must not swallow real photographers."""
    assert srg._commons_row(_page("CC BY-SA 4.0", artist="Ermell")) is not None


def test_commons_sourcing_keeps_only_admissible_photos_and_stops_at_n(monkeypatch):
    pages = {
        "1": _page("CC BY-NC 2.0", url="https://x/nc.jpg", pid=1),  # dropped: NC
        "2": _page("Public domain", url="https://x/pd.jpg", pid=2),  # dropped: digitised print
        "3": _page("CC0", url="https://x/3.jpg", pid=3),
        "4": _page("CC BY-SA 4.0", url="https://x/4.jpg", pid=4),
        "5": _page("CC BY 2.0", url="https://x/5.jpg", pid=5),
    }
    monkeypatch.setattr(srg, "_get", lambda url: {"query": {"pages": pages}})
    rows = srg._commons_photos("garden rose", 2)
    assert len(rows) == 2, "must stop at n even though three are admissible"
    assert all(r["license"] in srg.GALLERY_LICENSES for r in rows)
    got = [r["url"] for r in rows]
    assert "https://x/nc.jpg" not in got and "https://x/pd.jpg" not in got


def test_source_taxon_sends_rosa_to_commons_instead_of_inaturalist(monkeypatch):
    """The routing that unblocks the gallery. Rosa must NOT fall through to iNaturalist, whose
    only cultivated record is a single photo."""
    called = {"inat": False, "commons": False}

    def _inat(*a, **k):
        called["inat"] = True
        return []

    def _commons(search, n):
        called["commons"] = True
        assert "rose" in search.lower()
        return []

    monkeypatch.setattr(srg, "_cc_photos", _inat)
    monkeypatch.setattr(srg, "_commons_photos", _commons)
    monkeypatch.setattr(srg, "_resolve_taxon_id", lambda b: 47216)
    srg.source_taxon("Rosa", 8, True)
    assert called["commons"] is True
    assert called["inat"] is False


def test_every_other_taxon_still_comes_from_inaturalist(monkeypatch):
    """Positive control on the routing: Commons is a narrow escape hatch for taxa iNaturalist
    structurally cannot supply, not a replacement source."""
    called = {"inat": False, "commons": False}
    monkeypatch.setattr(srg, "_cc_photos", lambda *a, **k: called.__setitem__("inat", True) or [])
    monkeypatch.setattr(
        srg, "_commons_photos", lambda *a, **k: called.__setitem__("commons", True) or []
    )
    monkeypatch.setattr(srg, "_resolve_taxon_id", lambda b: 55779)
    srg.source_taxon("Pinus sylvestris", 8, True)
    assert called["inat"] is True
    assert called["commons"] is False


def test_the_commons_roster_is_only_the_taxon_inaturalist_cannot_supply():
    assert "Rosa" in srg.COMMONS_SEARCH
    assert "Solanum lycopersicum" not in srg.COMMONS_SEARCH
