"""Per-organism pages: the long-tail surface the corpus was already sitting on.

The site had no page about an organism. It had a page per MODEL (37 of them) and a flat task
list, so a search for "Zea mays 3D model" or "Amanita muscaria reconstruction" — the queries
this benchmark is uniquely able to answer, with almost nothing competing for them — landed on
nothing. Meanwhile the data for such a page was all in hand: the reference photographs, the
difficulty tier, the per-model outputs and their votes.

Two decisions are load-bearing here and are pinned by tests below.

**One page per ORGANISM, not per task.** Tasks 10 and 14 are both *Arabidopsis thaliana* (one
reconstruction, one botanical-plausibility); 12 and 16 are both *Zea mays*. A page per task
would put two near-identical pages in front of the same query, which splits the ranking between
them and leaves both weaker than one consolidated page. 20 active tasks collapse to 16
organisms.

**The organism key is the one that already exists.** `service._gallery_slug` derives
`zea_mays` from a task title and the reference galleries are keyed by it in production. The URL
form is the same key with hyphens, which is the conventional separator for a path segment. It
is a mapping, not a second scheme — the alternative is a parallel identifier that drifts, which
is the failure this repo keeps hitting.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from starlette.testclient import TestClient

from app import config, organisms
from app.database import SessionLocal, init_db
from app.main import app
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
def corpus(db, monkeypatch):
    """One organism carrying TWO tasks, plus a second organism, plus an app-hidden generator.

    Shaped to exercise the consolidation decision directly: `Testus organismus` gets both a
    reconstruction task and a plausibility task, exactly the arrangement that produced two
    competing pages for *Arabidopsis thaliana* under a per-task scheme.

    The hidden generator is hidden by adding its slug to `config.APP_HIDDEN_GENERATOR_SLUGS`,
    which is the production rule itself — `service.app_hidden_generator_ids` reads it live. A
    unique slug rather than a real member of that frozenset, because `Generator.slug` is UNIQUE
    and the real names are already seeded by neighbouring tests.
    """
    tag = uuid.uuid4().hex[:8]
    binomial = f"Testus organismus{tag}"
    other = f"Aliud animal{tag}"
    hidden_slug = f"org-hidden-{tag}"
    monkeypatch.setattr(
        config, "APP_HIDDEN_GENERATOR_SLUGS", config.APP_HIDDEN_GENERATOR_SLUGS | {hidden_slug}
    )

    cat = Category(slug=f"plants-{tag}", name="Plants Fixture")
    db.add(cat)
    db.flush()
    recon = Task(
        category_id=cat.id, title=f"{binomial} — single-image → 3D reconstruction", prompt="p"
    )
    plaus = Task(category_id=cat.id, title=f"{binomial} — botanical plausibility", prompt="p")
    solo = Task(category_id=cat.id, title=f"{other} — single-image → 3D reconstruction", prompt="p")
    visible = Generator(
        slug=f"org-visible-{tag}", name=f"OrgVisible {tag}", kind="model", paradigm="image_recon"
    )
    hidden = Generator(
        slug=hidden_slug, name=f"OrgHidden {tag}", kind="model", paradigm="image_recon"
    )
    db.add_all([recon, plaus, solo, visible, hidden])
    db.flush()
    for task in (recon, plaus, solo):
        for gen in (visible, hidden):
            db.add(
                ModelOutput(
                    task_id=task.id,
                    generator_id=gen.id,
                    asset_path=f"org/{tag}-{task.id}-{gen.id}.glb",
                    asset_format="glb",
                )
            )
    db.commit()
    task_ids = [recon.id, plaus.id, solo.id]
    gen_ids = [visible.id, hidden.id]
    try:
        yield {
            "binomial": binomial,
            "other": other,
            "slug": organisms.url_slug(binomial),
            "other_slug": organisms.url_slug(other),
            "visible_gen": visible.name,
            "hidden_gen": hidden.name,
            "hidden_slug": hidden_slug,
            "task_ids": task_ids,
        }
    finally:
        db.execute(delete(ModelOutput).where(ModelOutput.task_id.in_(task_ids)))
        db.execute(delete(Task).where(Task.id.in_(task_ids)))
        db.execute(delete(Generator).where(Generator.id.in_(gen_ids)))
        db.execute(delete(Category).where(Category.id == cat.id))
        db.commit()


# --- the key --------------------------------------------------------------------------


def test_the_url_slug_is_the_binomial_hyphenated():
    assert organisms.url_slug("Zea mays") == "zea-mays"
    assert organisms.url_slug("Canis lupus familiaris") == "canis-lupus-familiaris"
    assert organisms.url_slug("Rosa") == "rosa"


def test_the_url_slug_and_the_gallery_slug_are_the_same_key():
    """The galleries on disk are keyed `zea_mays`; the URL is `zea-mays`. If these two ever
    stop being a pure separator swap, an organism page silently renders with no photographs."""
    for binomial in ("Zea mays", "Hericium erinaceus", "Canis lupus familiaris", "Rosa"):
        from app.service import _gallery_slug

        assert organisms.gallery_slug(organisms.url_slug(binomial)) == _gallery_slug(binomial)


def test_the_binomial_is_read_off_the_task_title_the_same_way_the_rest_of_the_app_reads_it():
    """`title.split("—")[0]` is the convention in app/difficulty.py, app/service.py and the
    gallery sourcing scripts. A second parser here would be a fourth copy."""
    assert organisms.binomial_of_title("Zea mays — single-image → 3D reconstruction") == "Zea mays"
    assert organisms.binomial_of_title("Zea mays — botanical plausibility") == "Zea mays"


# --- consolidation --------------------------------------------------------------------


def test_two_tasks_for_one_organism_produce_one_page_not_two(db, corpus):
    """The whole reason this is an organism page and not a task page."""
    org = organisms.build_organism(db, corpus["slug"])
    assert org is not None, "organism page did not build"
    titles = [t["title"] for t in org["tasks"]]
    assert len(titles) == 2, f"expected both tasks consolidated onto one page, got {titles}"
    assert any("reconstruction" in t for t in titles)
    assert any("plausibility" in t for t in titles)


def test_a_different_organism_is_a_different_page(db, corpus):
    """Positive control for the test above: consolidation must not collapse everything."""
    org = organisms.build_organism(db, corpus["other_slug"])
    assert org is not None
    assert len(org["tasks"]) == 1
    assert org["binomial"] == corpus["other"]


def test_an_unknown_organism_has_no_page(db):
    assert organisms.build_organism(db, "not-a-real-organism") is None


# --- what the page reports ------------------------------------------------------------


def test_the_page_credits_every_model_that_attempted_the_organism(db, corpus):
    org = organisms.build_organism(db, corpus["slug"])
    names = [m["name"] for m in org["models"]]
    assert corpus["visible_gen"] in names


def test_an_app_hidden_generator_is_not_named_on_the_page(db, corpus):
    """Same rule as /models and the sitemap: internal testers stay internal. Read from
    `service.app_hidden_generator_ids` rather than restated here."""
    org = organisms.build_organism(db, corpus["slug"])
    names = [m["name"] for m in org["models"]]
    assert corpus["hidden_gen"] not in names, "app-hidden generator surfaced on an organism page"
    # Positive control: the visible one IS named, so this is not passing on an empty list.
    assert corpus["visible_gen"] in names


def test_output_counts_cover_both_tasks(db, corpus):
    """The consolidated page reports the organism's whole record, not one task's."""
    org = organisms.build_organism(db, corpus["slug"])
    visible = next(m for m in org["models"] if m["name"] == corpus["visible_gen"])
    assert visible["outputs"] == 2, "one output per task, both tasks counted"


# --- the routes -----------------------------------------------------------------------


def test_the_organism_page_serves_and_names_the_organism(client, corpus):
    r = client.get(f"/organisms/{corpus['slug']}")
    assert r.status_code == 200
    assert corpus["binomial"] in r.text


def test_an_unknown_organism_url_404s(client):
    assert client.get("/organisms/not-a-real-organism").status_code == 404


def test_the_organism_index_links_to_every_organism_page(client, corpus):
    """Crawlers reach these pages by following links as well as by reading the sitemap, so the
    index is what keeps a new organism discoverable without a sitemap fetch."""
    r = client.get("/organisms")
    assert r.status_code == 200
    assert f"/organisms/{corpus['slug']}" in r.text
    assert f"/organisms/{corpus['other_slug']}" in r.text


def test_the_organism_page_carries_its_own_meta_description(client, corpus):
    """A page indexed without a description gets a snippet invented for it by the crawler."""
    import re

    body = client.get(f"/organisms/{corpus['slug']}").text
    m = re.search(r'name="description"\s*content="(.*?)"', body, re.S)
    assert m, "organism page has no meta description"
    assert corpus["binomial"] in m.group(1), "meta description does not name the organism"


def test_the_description_never_claims_a_rank(db, corpus):
    """Nothing on this site may assert a standing the leaderboard declines to assert. Every
    entrant is currently unranked for want of votes, and a meta description is the one piece of
    page text that gets quoted verbatim into a search result, where no caveat travels with it.
    """
    org = organisms.build_organism(db, corpus["slug"])
    text = organisms.meta_description(org).lower()
    for claim in ("best", "#1", "rank", "top ", "winner", "beats", "outperform"):
        assert claim not in text, f"meta description makes a standing claim: {claim!r}"


def test_the_task_catalog_links_each_subject_to_its_organism_page(client, corpus):
    """Internal links, not just the sitemap, are how these pages accumulate standing.

    A sitemap tells a crawler a URL exists; a link from an existing page tells it the URL
    matters and, through the anchor text, what it is about. The task catalog already prints
    every subject, so linking each one is the highest-value place to put that link — and it
    means an organism added to the corpus is reachable the moment its task is.
    """
    body = client.get("/tasks").text
    assert f"/organisms/{corpus['slug']}" in body, "task catalog does not link to organism pages"


def test_the_task_catalog_does_not_link_a_retired_organism(client, db, corpus):
    """`/tasks` lists RETIRED tasks as well as live ones; organism pages cover only the live
    corpus. Linking the subject of every row therefore invents links to pages that do not
    exist — measured against the real corpus this produced two, `cucurbita-pepo` (the
    de-corpused pumpkin) and `hordeum-vulgare`.

    Caught only by rendering against the real database: the fixtures are all active, so the
    synthetic tests could not see it.
    """
    import re

    # The SECOND organism is the one to retire: it has exactly one task, so deactivating that
    # task leaves it with no live task at all and therefore no page — while it stays on /tasks.
    # Retiring one of the two-task organism's tasks would prove nothing, since its sibling task
    # keeps the page alive.
    solo_id = corpus["task_ids"][-1]
    solo = db.get(Task, solo_id)
    solo.active = False
    db.commit()
    try:
        assert client.get(f"/organisms/{corpus['other_slug']}").status_code == 404, (
            "fixture is wrong: the retired organism still has a page"
        )
        body = client.get("/tasks").text
        assert corpus["other"] in body, "positive control: retired task still listed on /tasks"
        linked = set(re.findall(r'/organisms/([a-z0-9-]+)"', body))
        dead = [s for s in linked if client.get(f"/organisms/{s}").status_code != 200]
        assert dead == [], f"task catalog links to organism pages that do not exist: {dead}"
    finally:
        solo.active = True
        db.commit()


# --- indexing -------------------------------------------------------------------------


def test_every_organism_page_is_in_the_sitemap(client, db, corpus):
    body = client.get("/sitemap.xml").text
    for row in organisms.organism_index(db):
        assert f"/organisms/{row['slug']}</loc>" in body, f"{row['slug']} missing from sitemap"


def test_the_organism_index_is_in_the_sitemap(client):
    assert "/organisms</loc>" in client.get("/sitemap.xml").text


def test_the_organism_page_emits_breadcrumb_structured_data(client, corpus):
    """Breadcrumbs are the honest structured-data claim for this page.

    Deliberately NOT schema.org/Dataset, which is what a page angling for Google Dataset Search
    would emit: a Dataset is expected to describe something downloadable, and an organism page
    offers no distribution. Marking it up as one to win a richer search result would be a claim
    about the page that isn't true — the same standard the leaderboard is held to.
    """
    import json
    import re

    body = client.get(f"/organisms/{corpus['slug']}").text
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    assert blocks, "organism page emits no JSON-LD"
    kinds = [json.loads(b)["@type"] for b in blocks]
    assert "BreadcrumbList" in kinds, f"no BreadcrumbList in {kinds}"
    assert "Dataset" not in kinds, "organism page must not claim to be a downloadable Dataset"
