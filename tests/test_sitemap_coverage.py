"""The sitemap advertised 12 top-level paths and none of the pages that hold the content.

Measured against the live instance on 2026-07-30: `/sitemap.xml` listed exactly the 12 entries
of `_SITEMAP_PATHS`, while 37 `/models/{slug}` detail pages and 4 `/leaderboard/{modality}`
boards were serving 200 with unique titles, per-model meta descriptions and real per-task
tables. Every one of them was reachable only by crawling inward from `/models`, and none was
declared to a crawler.

So the sitemap becomes a query rather than a constant. The two visibility rules it has to
respect already exist and are enforced by the routes themselves:

  * `/models/{slug}` 404s for an app-hidden generator (`service.app_hidden_generator_ids`), and
    `_model_cards` additionally drops generators with no coverage row (gold-only / empty).
  * `/leaderboard/{modality}` 404s for `config.APP_HIDDEN_PARADIGMS` and for anything that is
    not a known paradigm.

Both are read from their existing single source here rather than restated. This codebase has
been bitten repeatedly by a predicate copied to a second site and then drifting — most recently
a third copy of the licence allowlist that silently starved the rosa reference gallery — and a
sitemap that disagrees with its routes is exactly that bug pointed at crawlers.

The generalised guard is `test_every_url_in_the_sitemap_resolves`: whatever the sitemap grows
to next, it may not name a URL the app 404s. That is the lesson /spotlight taught (see
tests/test_spotlight_is_internal.py) applied to the dynamic entries.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import delete
from starlette.testclient import TestClient

from app import config
from app.database import SessionLocal, init_db
from app.main import _model_cards, app
from app.models import Category, Generator, ModelOutput, Task


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    init_db()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def roster(db, monkeypatch):
    """A visible generator and an app-hidden one, both with real outputs.

    The suite runs against an empty temp database (see conftest.py), so a test that read the
    roster off whatever happened to be seeded would SKIP rather than fail — and a test that
    cannot fail is not a test. These rows are committed, because the assertions go through
    TestClient and the route opens its own session; they are removed again on teardown so they
    do not leak into the rest of the run.

    Both generators get a per-call unique slug, and the hidden one is hidden by ADDING that
    slug to `config.APP_HIDDEN_GENERATOR_SLUGS` — which is the production rule itself, read
    live by `service.app_hidden_generator_ids`. Naming a real member of that frozenset instead
    (`agrigen`) is what the first draft did, and it collided on `Generator.slug`'s UNIQUE
    constraint the moment the full suite ran alongside another test that seeds the same slug.
    """
    tag = uuid.uuid4().hex[:8]
    hidden_slug = f"sitemap-hidden-{tag}"
    monkeypatch.setattr(
        config, "APP_HIDDEN_GENERATOR_SLUGS", config.APP_HIDDEN_GENERATOR_SLUGS | {hidden_slug}
    )
    cat = Category(slug=f"sm-{tag}", name="Sitemap Fixture")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"Sitemap fixture {tag}", prompt="p")
    visible = Generator(
        slug=f"sitemap-visible-{tag}", name=f"Visible {tag}", kind="model", paradigm="image_recon"
    )
    hidden = Generator(
        slug=hidden_slug, name=f"Hidden tester {tag}", kind="model", paradigm="image_recon"
    )
    db.add_all([task, visible, hidden])
    db.flush()
    for gen in (visible, hidden):
        db.add(
            ModelOutput(
                task_id=task.id,
                generator_id=gen.id,
                asset_path=f"sitemap/{tag}-{gen.id}.glb",
                asset_format="glb",
            )
        )
    db.commit()
    ids = (visible.id, hidden.id)
    try:
        yield {"visible": visible.slug, "hidden": hidden.slug}
    finally:
        db.execute(delete(ModelOutput).where(ModelOutput.generator_id.in_(ids)))
        db.execute(delete(Generator).where(Generator.id.in_(ids)))
        db.execute(delete(Task).where(Task.id == task.id))
        db.execute(delete(Category).where(Category.id == cat.id))
        db.commit()


def sitemap_paths(client) -> list[str]:
    """Every <loc> in the sitemap, reduced to a site-relative path."""
    body = client.get("/sitemap.xml").text
    locs = re.findall(r"<loc>(.*?)</loc>", body)
    return [loc[len(config.PUBLIC_BASE_URL) :] for loc in locs]


# --- model detail pages -------------------------------------------------------------


def test_sitemap_lists_every_publicly_visible_model_page(client, db, roster):
    """The 37 model pages were the single largest block of unindexed content on the site."""
    visible = [c["slug"] for c in _model_cards(db, None) if c["slug"]]
    assert roster["visible"] in visible, "fixture generator is not publicly visible — bad fixture"
    listed = set(sitemap_paths(client))
    missing = [s for s in visible if f"/models/{s}" not in listed]
    assert missing == [], f"model pages missing from sitemap: {missing[:5]}"


def test_sitemap_does_not_advertise_an_app_hidden_generator(client, roster):
    """AgriGen's internal testers are kept in the DB for analysis but 404 by URL, so listing
    one would hand crawlers a dead link — and would leak the tester's slug besides."""
    hidden = roster["hidden"]
    # The fixture's hidden generator really is hidden by the production rule, not by assumption.
    assert client.get(f"/models/{hidden}").status_code == 404
    listed = set(sitemap_paths(client))
    assert f"/models/{hidden}" not in listed, f"app-hidden generator leaked into sitemap: {hidden}"
    # Positive control: the sitemap does list model pages, so the assertion above is not
    # passing merely because nothing at all was listed.
    assert f"/models/{roster['visible']}" in listed, "no model pages in sitemap to speak of"


# --- modality boards ----------------------------------------------------------------


def test_sitemap_lists_the_board_of_a_modality_that_has_entrants(client, roster):
    """The fixture's visible generator is `image_recon`, so that board has something to show."""
    listed = set(sitemap_paths(client))
    assert "/leaderboard/image_recon" in listed, "populated modality board missing from sitemap"


def test_sitemap_omits_the_board_of_a_modality_with_no_entrants(client, roster):
    """`paradigms.PARADIGMS` carries reserved names — video, texturing, sketch — that no
    generator has been tagged with yet. Their boards render, but they render EMPTY, and a
    sitemap full of empty pages is thin content: it spends crawl budget to advertise nothing
    and drags the average quality of what is indexed. A board earns its entry by having
    entrants, so these appear on their own the moment a generator is tagged with one.
    """
    listed = set(sitemap_paths(client))
    unpopulated = [p for p in ("video", "texturing", "sketch") if f"/leaderboard/{p}" in listed]
    assert unpopulated == [], f"empty modality boards advertised to crawlers: {unpopulated}"
    # Positive control: a populated board IS listed, so this is not passing because the
    # sitemap simply contains no boards at all.
    assert "/leaderboard/image_recon" in listed, "no modality boards in sitemap to speak of"


def test_sitemap_omits_the_app_hidden_modality_boards(client, roster):
    """`/leaderboard/retrieval` and friends 404 on purpose — an internal-only modality must
    not exist as a public surface, so it must not be advertised as one either."""
    listed = set(sitemap_paths(client))
    leaked = [p for p in config.APP_HIDDEN_PARADIGMS if f"/leaderboard/{p}" in listed]
    assert leaked == [], f"app-hidden modality boards leaked into the sitemap: {leaked}"
    # Positive control, same reasoning as above.
    assert any(p.startswith("/leaderboard/") for p in listed), "no modality boards in sitemap"


# --- the generalised guards ---------------------------------------------------------


def test_the_sitemap_is_well_formed_xml_even_when_a_slug_contains_markup(client, db):
    """A data-driven sitemap can be broken by its own data; a hand-written one cannot.

    `<loc>` interpolates a generator slug straight into XML, so a slug carrying `&` or `<`
    emits a malformed document — and a malformed sitemap does not degrade to "one bad entry",
    it fails whole: the parser stops and every URL in it goes unread. Nothing in the schema
    constrains `Generator.slug` to XML-safe characters, and the live roster already carries
    punctuation (`fal:trellis`), so this is a matter of what gets ingested next rather than a
    hypothetical.
    """
    import xml.etree.ElementTree as ET

    tag = uuid.uuid4().hex[:8]
    cat = Category(slug=f"xml-{tag}", name="XML Fixture")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"XML fixture {tag}", prompt="p")
    gen = Generator(
        slug=f"a&b<c-{tag}", name=f"Ampersand {tag}", kind="model", paradigm="image_recon"
    )
    db.add_all([task, gen])
    db.flush()
    db.add(
        ModelOutput(
            task_id=task.id,
            generator_id=gen.id,
            asset_path=f"xml/{tag}.glb",
            asset_format="glb",
        )
    )
    db.commit()
    try:
        body = client.get("/sitemap.xml").text
        ET.fromstring(body)  # raises ParseError if the slug broke the document
    finally:
        db.execute(delete(ModelOutput).where(ModelOutput.generator_id == gen.id))
        db.execute(delete(Generator).where(Generator.id == gen.id))
        db.execute(delete(Task).where(Task.id == task.id))
        db.execute(delete(Category).where(Category.id == cat.id))
        db.commit()


def test_every_url_in_the_sitemap_resolves(client):
    """No entry may 404. This is the rule /spotlight broke, generalised to the dynamic
    entries so a future roster change cannot quietly reintroduce it."""
    dead = []
    for path in sitemap_paths(client):
        status = client.get(path).status_code
        if status == 404:
            dead.append((path, status))
    assert dead == [], f"sitemap advertises URLs that 404: {dead[:5]}"


def test_the_static_top_level_paths_are_still_listed(client):
    """Making the sitemap a query must not drop the pages it already had."""
    listed = set(sitemap_paths(client))
    for path in ("/", "/arena", "/leaderboard", "/models", "/dataset", "/methodology", "/tasks"):
        assert path in listed, f"{path} dropped from sitemap"
