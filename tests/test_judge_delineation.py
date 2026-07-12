"""`/leaderboard/judge` — the AI-judge board is a SEPARATE, clearly-labelled surface.

Two consumers, one route:

1. A BROWSER lands here from the "see the AI-judge board →" link on a modality board. It must
   get a real page (site chrome, title, nav, styling) that says, in plain language, that these
   ranks come from a VLM judge and NOT from human votes — a reader must never mistake it for the
   human board.
2. `app/static/leaderboard.js` lazy-fetches the same route into the collapsed <details> on the
   leaderboard (the judge BT fit is ~11s cold, so it is deliberately not computed on the main
   render). That consumer needs a BARE FRAGMENT, not a page.

The fragment is selected explicitly (`?fragment=1`, which is what the rendered `data-judge-url`
carries) or by the `X-Requested-With` header leaderboard.js already sends. Everything else — a
plain browser navigation — gets the page. Both paths are asserted below.

Judge ranks and human ranks are never intermixed: the page ranks within ONE paradigm at a time
and links back to that paradigm's HUMAN board.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import config, paradigms
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Criterion, Generator, JudgeRating

client = TestClient(app)

# (slug, paradigm, bt_score, n_games) — cached global judge ratings (category_id=None,
# view_condition="multi4"), which is the row source for the default kingdom="all" scope.
FIXTURES = [
    ("judgedel-recon", "image_recon", 8400.0, 30),
    ("judgedel-text", "text_native", 8300.0, 20),
    ("judgedel-scan", "capture_scan", 9999.0, 99),  # app-hidden: must never surface
]


def setup_module(_m):
    init_db()
    with SessionLocal() as db:
        crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        if crit is None:
            crit = Criterion(slug="overall", name="Overall")
            db.add(crit)
            db.flush()
        for slug, paradigm, bt, n_games in FIXTURES:
            if db.execute(select(Generator).where(Generator.slug == slug)).scalars().first():
                continue
            g = Generator(slug=slug, name=slug, kind="model", paradigm=paradigm)
            db.add(g)
            db.flush()
            db.add(
                JudgeRating(
                    generator_id=g.id,
                    category_id=None,
                    criterion_id=crit.id,
                    view_condition="multi4",
                    elo=1000.0,
                    bt_score=bt,
                    bt_lower=bt - 1.0,
                    bt_upper=bt + 1.0,
                    n_games=n_games,
                )
            )
        db.commit()


def teardown_module(_m):
    with SessionLocal() as db:
        for slug, *_ in FIXTURES:
            g = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
            if g is None:
                continue
            db.execute(delete(JudgeRating).where(JudgeRating.generator_id == g.id))
            db.delete(g)
        db.commit()


# ------------------------------------------------------------------ consumer 1: the browser


def test_judge_route_renders_a_full_page_not_a_bare_fragment():
    """The board's link is a normal href — landing on it must not dump an unstyled fragment."""
    r = client.get("/leaderboard/judge")
    assert r.status_code == 200
    html = r.text
    assert "<html" in html.lower()  # site chrome
    assert "<title>" in html.lower()
    assert "/leaderboard" in html  # nav is present


def test_judge_page_is_labeled_as_vlm_judge_not_human_votes():
    html = client.get("/leaderboard/judge").text
    assert "VLM judge" in html
    assert "not human votes" in html


def test_judge_page_links_back_to_the_human_board_for_the_selected_modality():
    html = client.get("/leaderboard/judge?modality=image_recon").text
    assert "/leaderboard/image_recon" in html
    assert "human" in html.lower()


# --------------------------------------------------- consumer 2: the leaderboard.js lazy fetch


def _lazy_judge_url() -> str:
    """The URL leaderboard.js actually fetches — read off the rendered lazy container rather
    than hard-coded, so this test tracks the real client contract."""
    html = client.get("/leaderboard").text
    m = re.search(r'data-judge-url="([^"]+)"', html)
    assert m, "the lazy judge container is missing from the leaderboard render"
    return m.group(1).replace("&amp;", "&")


def test_lazy_container_url_still_returns_a_bare_fragment():
    """leaderboard.js does `body.innerHTML = frag` — a full page there would nest <html> inside
    a <div>. The URL on data-judge-url must therefore stay a fragment."""
    url = _lazy_judge_url()
    r = client.get(url, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert "<html" not in r.text.lower()
    assert "<title" not in r.text.lower()


def test_xhr_header_alone_selects_the_fragment():
    """Belt-and-braces: the header leaderboard.js sends is honored even without ?fragment=1."""
    r = client.get("/leaderboard/judge", headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    assert "<html" not in r.text.lower()


def test_fragment_and_page_show_the_same_judge_rows():
    """One board, two wrappers — the fragment is the page's table, not a different ranking."""
    page = client.get("/leaderboard/judge?modality=image_recon").text
    frag = client.get("/leaderboard/judge?modality=image_recon&fragment=1").text
    assert "judgedel-recon" in page
    assert "judgedel-recon" in frag
    assert "<html" not in frag.lower()


# ---------------------------------------------------------------- modality scoping + hiding


def test_modality_param_selects_that_modalitys_judge_ranking():
    html = client.get("/leaderboard/judge?modality=image_recon").text
    assert paradigms.DISPLAY_NAMES["image_recon"] in html
    assert "judgedel-recon" in html
    assert "judgedel-text" not in html  # another modality's rows are never intermixed


def test_modality_switcher_offers_the_visible_modalities():
    html = client.get("/leaderboard/judge?modality=image_recon").text
    for p in ("image_recon", "text_native"):
        assert f"/leaderboard/judge?modality={p}" in html or f"modality={p}" in html


def test_hidden_paradigms_never_appear_on_the_judge_page():
    for url in ("/leaderboard/judge", "/leaderboard/judge?modality=image_recon"):
        html = client.get(url).text
        for hidden in config.APP_HIDDEN_PARADIGMS:
            assert paradigms.DISPLAY_NAMES[hidden] not in html, (url, hidden)
        assert "judgedel-scan" not in html


def test_hidden_or_unknown_modality_is_404():
    for hidden in config.APP_HIDDEN_PARADIGMS:
        r = client.get(f"/leaderboard/judge?modality={hidden}")
        assert r.status_code == 404, hidden
        assert "judgedel-scan" not in r.text
    assert client.get("/leaderboard/judge?modality=not_a_paradigm").status_code == 404


def test_no_modality_shows_every_visible_modality_grouped_never_merged():
    """Without a modality the page still groups BY paradigm — there is no cross-paradigm judge
    ranking (disconnected match pools), so rows are never merged into one table."""
    html = client.get("/leaderboard/judge").text
    assert paradigms.DISPLAY_NAMES["image_recon"] in html
    assert paradigms.DISPLAY_NAMES["text_native"] in html
    assert html.count('<table class="ranktable">') >= 2
