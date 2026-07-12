"""TDD for the Models index (`/models`) + detail (`/models/{slug}`) pages (Task 12).

Real generators only (no fabricated org/company field — `Generator` has none); stats are
reused from `service.coverage_summary` + `_leaderboard_rows`, not hand-rolled.

The directory is SECTIONED BY METHOD and ranked WITHIN a method. It used to be one flat grid
sorted by the MERGED (cross-paradigm) BT number, printed uncaveated on every card — a
cross-paradigm ranking on a top-level nav page, and the last one on the site (paradigms are
disconnected match pools: `service._matches_for_scope` never pairs across them, so a merged BT
ordering is not a statistical claim).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main, paradigms
from app.database import SessionLocal
from app.main import app
from app.models import Criterion, Generator, Rating
from app.seed import seed_all

client = TestClient(app)

# Seeded generators carry no paradigm, so the page would be a single "Unclassified" section.
# Tag them into TWO modalities and give one model in EACH a vote history, so the cross-method
# leak has something to leak across: on the merged fit, alpha (1100) outranks gamma (900) and the
# flat grid printed them in that single order.
PARADIGM_BY_SLUG = {
    "gen-alpha": "image_recon",
    "gen-beta": "image_recon",
    "gen-gamma": "text_native",
    "gen-delta": "text_native",
}
RATED = {"gen-alpha": (1100.0, 8), "gen-gamma": (900.0, 8)}


def setup_module(_module):
    seed_all(force=True)
    with SessionLocal() as db:
        crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        for slug, pgm in PARADIGM_BY_SLUG.items():
            g = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
            g.paradigm = pgm
            if slug in RATED:
                bt, n = RATED[slug]
                r = (
                    db.execute(
                        select(Rating).where(
                            Rating.generator_id == g.id,
                            Rating.criterion_id == crit.id,
                            Rating.category_id.is_(None),
                        )
                    )
                    .scalars()
                    .first()
                )
                r.bt_score, r.bt_lower, r.bt_upper, r.n_games = bt, bt - 20.0, bt + 20.0, n
        db.commit()


def teardown_module(_module):
    """Undo the paradigm tags + vote history: the suite shares one DB file, and a rated
    image_recon/text_native generator left behind would change later modules' boards."""
    with SessionLocal() as db:
        for slug in PARADIGM_BY_SLUG:
            g = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
            if g is None:
                continue
            g.paradigm = ""  # the seeded (un-backfilled) value; the column is NOT NULL
            for r in db.execute(select(Rating).where(Rating.generator_id == g.id)).scalars().all():
                r.bt_score, r.bt_lower, r.bt_upper, r.n_games = 0.0, 0.0, 0.0, 0
        db.commit()


def test_models_index_200_lists_known_generator():
    r = client.get("/models")
    assert r.status_code == 200
    assert "Generator Alpha" in r.text


def test_models_index_omits_scope_pill_per_design():
    # Design (04-models.png): the Models title carries NO kingdom scope pill — the pill is
    # data-page chrome, and Models spans all generators regardless of kingdom scope.
    r = client.get("/models")
    assert r.status_code == 200
    assert "b3d-scope-pill" not in r.text


def test_model_detail_200_for_known_slug():
    r = client.get("/models/gen-alpha")
    assert r.status_code == 200
    assert "Generator Alpha" in r.text


def test_model_detail_404_for_unknown_slug():
    r = client.get("/models/nonexistent-generator-slug")
    assert r.status_code == 404


def test_models_rated_only_default_with_show_all_toggle():
    """Default grid hides never-voted (0-vote) generators; ?show_all=true reveals them, and
    the toggle is offered whenever anything is hidden."""
    default = client.get("/models")
    show_all = client.get("/models?show_all=true")
    assert default.status_code == 200 and show_all.status_code == 200
    n_default = default.text.count('class="b3d-model-card"')
    n_all = show_all.text.count('class="b3d-model-card"')
    assert n_all >= n_default
    if n_all > n_default:
        assert "Show all" in default.text
        assert "Show rated only" in show_all.text


# ---------------------------------------------- /models is grouped + ranked BY METHOD, not merged


def _section(html: str, display: str) -> str:
    """The markup of one method's section (up to the next section)."""
    return html.split(f"<h2>{display}</h2>")[1].split("<h2>")[0]


def test_models_index_is_sectioned_by_method():
    html = client.get("/models?show_all=true").text
    assert 'class="b3d-model-section"' in html
    for p in ("image_recon", "text_native"):
        assert f"<h2>{paradigms.DISPLAY_NAMES[p]}</h2>" in html
        assert f"/leaderboard/{p}" in html  # each section links to that method's board


def test_models_grid_does_not_interleave_methods_in_one_bt_order():
    """The leak: one grid sorted by the merged BT number. alpha (image_recon, BT 1100) outranks
    gamma (text_native, BT 900) on the merged fit, so the flat grid put them in that single
    descending order with no caveat — a cross-method ranking. Each model must now live inside its
    OWN method's section."""
    html = client.get("/models?show_all=true").text
    recon = _section(html, paradigms.DISPLAY_NAMES["image_recon"])
    text = _section(html, paradigms.DISPLAY_NAMES["text_native"])
    assert "Generator Alpha" in recon and "Generator Beta" in recon
    assert "Generator Alpha" not in text and "Generator Beta" not in text
    assert "Generator Gamma" in text and "Generator Delta" in text
    assert "Generator Gamma" not in recon


def test_each_method_section_ranks_from_one():
    """Ranks are computed WITHIN a method (service.finalize_rows over that modality's rated rows —
    no BT refit): every section's rated leader is #1. On the merged ordering gamma would be #2."""
    with SessionLocal() as db:
        cards = main._model_cards(db, None)
    by_slug = {c["slug"]: c for c in cards}
    assert by_slug["gen-alpha"]["rank"] == 1  # image_recon leader
    assert by_slug["gen-gamma"]["rank"] == 1  # text_native leader — NOT 2
    assert by_slug["gen-beta"]["rank"] is None  # unrated: carries only the prior, so no rank
    sections = main._model_sections(cards, show_all=True)
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    got = [s["paradigm"] for s in sections]
    assert got[: got.index(None) if None in got else len(got)] == sorted(
        (p for p in got if p), key=lambda p: order[p]
    )  # method sections in registry order; "Unclassified" (None) last


def test_bt_number_is_labelled_a_within_method_score():
    html = client.get("/models?show_all=true").text
    assert "Bradley–Terry overall score" not in html  # the uncaveated cross-method label is gone
    assert "in method" in html
    assert "aren't comparable" in html
    detail = client.get("/models/gen-alpha").text
    assert "Bradley–Terry overall score" not in detail
    assert f"Bradley–Terry score within {paradigms.DISPLAY_NAMES['image_recon']}" in detail
    assert "#1" in detail  # its rank, explicitly within its own method


def test_unvoted_method_section_says_evaluation_in_progress(monkeypatch):
    """Mirrors the hub: in the rated-only default view, a method whose models are all unrated keeps
    its section (with an honest empty state) instead of vanishing from the directory."""
    html = client.get("/models").text  # rated-only: beta/delta hidden, both sections still shown
    assert f"<h2>{paradigms.DISPLAY_NAMES['image_recon']}</h2>" in html
    assert f"<h2>{paradigms.DISPLAY_NAMES['text_native']}</h2>" in html
    cards = [
        {
            "slug": "x",
            "name": "X",
            "paradigm": "agentic",
            "bt_score": None,
            "rank": None,
            "votes": 0,
        }
    ]
    section = main._model_sections(cards, show_all=False)[0]
    assert section["cards"] == [] and section["model_count"] == 1 and section["rated_count"] == 0
    assert section["board_url"] == "/leaderboard/agentic"
