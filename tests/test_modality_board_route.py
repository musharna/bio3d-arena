"""`GET /leaderboard/{modality}` — the per-modality human-vote board reached from the hub.

The path route is a thin, VALIDATED alias for the existing `?paradigm=X` board: an unknown
paradigm or an app-hidden one (config.APP_HIDDEN_PARADIGMS) must 404, never render a board.
It also carries the board's "what this measures" header, the AI-judge delineation link (the
judge board is a SEPARATE surface — its ranks are never intermixed with the human board), and
the per-row votes-until-firm status, so a low-vote rank reads as evaluation-in-progress rather
than settled.

Route-ordering guard: `/leaderboard/judge` is declared BEFORE `/leaderboard/{modality}`, so
"judge" is never swallowed by the path param. `test_judge_route_is_not_shadowed` locks that in.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import config, paradigms, service
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Criterion, Generator, Rating

client = TestClient(app)

# (slug, paradigm, bt_score, n_games). BT scores sit far above other modules' fixtures so these
# rows own the top of their modality's board regardless of test ordering (the suite shares one
# DB file); teardown_module drops them again so they never pollute another module's ranks.
FIXTURES = [
    ("modroute-recon-firm", "image_recon", 7400.0, service.FIRM_VOTE_THRESHOLD + 10),
    ("modroute-recon-one-away", "image_recon", 7300.0, service.FIRM_VOTE_THRESHOLD - 1),
    ("modroute-recon-cold", "image_recon", 7200.0, 5),
    ("modroute-recon-unrated", "image_recon", 7100.0, 0),  # rated-only board hides it
    ("modroute-text", "text_native", 7000.0, 12),  # other modality: never on the recon board
    ("modroute-scan", "capture_scan", 9999.0, 99),  # app-hidden: board must 404
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
                Rating(
                    criterion_id=crit.id,
                    category_id=None,
                    generator_id=g.id,
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
            db.execute(delete(Rating).where(Rating.generator_id == g.id))
            db.delete(g)
        db.commit()


# ------------------------------------------------------------------- the board renders


def test_modality_board_renders_single_paradigm():
    r = client.get("/leaderboard/image_recon")
    assert r.status_code == 200
    assert paradigms.DISPLAY_NAMES["image_recon"] in r.text
    assert 'class="lb-hub"' not in r.text  # a board, not the hub
    assert "modroute-recon-firm" in r.text
    assert "modroute-text" not in r.text  # exactly ONE paradigm per board


def test_board_carries_the_what_this_measures_header():
    html = client.get("/leaderboard/image_recon").text
    assert paradigms.WHAT_THIS_MEASURES["image_recon"] in html


def test_board_links_out_to_the_separate_ai_judge_board():
    html = client.get("/leaderboard/image_recon").text
    assert "see the AI-judge board" in html
    assert "/leaderboard/judge?" in html
    assert "modality=image_recon" in html


def test_board_shows_votes_until_firm_status_per_row():
    html = client.get("/leaderboard/image_recon").text
    assert "Status" in html  # the column header
    # one vote short of the threshold -> singular countdown, not "settled" and not "broken"
    assert service.firm_status(service.FIRM_VOTE_THRESHOLD - 1)["label"] in html
    assert service.firm_status(5)["label"] in html
    assert service.firm_status(service.FIRM_VOTE_THRESHOLD + 10)["label"] in html


def _board_body(html: str) -> str:
    """The rendered board, minus the <head> — whose canonical/OG tags are derived from the
    request URL and so legitimately differ between the path and query forms."""
    return html.split('<section class="lb">')[1]


def test_path_route_matches_the_paradigm_query_board():
    """The path route DELEGATES to the existing `?paradigm=` handler — same board, same rows."""
    path = client.get("/leaderboard/image_recon").text
    query = client.get("/leaderboard?paradigm=image_recon").text
    assert _board_body(path) == _board_body(query)


def test_query_params_are_preserved():
    default = client.get("/leaderboard/image_recon").text
    assert "modroute-recon-unrated" not in default  # rated-only by default
    shown = client.get("/leaderboard/image_recon?show_all=true").text
    assert "modroute-recon-unrated" in shown  # show_all reached the delegated handler
    # criterion/category ride through too (an unknown criterion yields no ratings rows)
    empty = client.get("/leaderboard/image_recon?criterion=nope")
    assert empty.status_code == 200
    assert "modroute-recon-firm" not in empty.text


# ------------------------------------------------------------------------ 404 / hiding


def test_hidden_or_unknown_modality_is_404():
    for hidden in config.APP_HIDDEN_PARADIGMS:
        assert client.get(f"/leaderboard/{hidden}").status_code == 404, hidden
    assert client.get("/leaderboard/capture_scan").status_code == 404
    assert client.get("/leaderboard/retrieval").status_code == 404
    assert client.get("/leaderboard/not_a_paradigm").status_code == 404


def test_hidden_modality_never_leaks_rows_through_the_404():
    r = client.get("/leaderboard/capture_scan")
    assert r.status_code == 404
    assert "modroute-scan" not in r.text


def test_judge_route_is_not_shadowed_by_the_modality_path():
    """`judge` is not a paradigm — if `/leaderboard/{modality}` were declared first it would
    swallow this path and 404 the judge board."""
    r = client.get("/leaderboard/judge")
    assert r.status_code == 200
