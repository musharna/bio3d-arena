"""/leaderboard is a modality HUB: one card per VISIBLE modality, each linking to that
modality's own board. The cross-paradigm "Overall" ranking is gone (paradigms are disconnected
match pools — a merged BT ordering was never a statistical claim). `?paradigm=X` renders a single
within-paradigm board; `?verified=true` is a SCOPE MODIFIER over the same two surfaces (hub /
one-paradigm board), never a merged cross-paradigm board of its own."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import config, paradigms, service
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Criterion, Generator, Rating

client = TestClient(app)

# (slug, paradigm, bt_score, n_games). BT scores sit far above every other test module's
# fixtures (~1000-2000) so these rows own the top of their modality's board even though the whole
# suite shares one DB file — the top-3 assertions must not depend on test ordering.
FIXTURES = [
    # UNRATED (n_games=0) but carrying the highest prior BT in its paradigm: ranks are assigned
    # over the whole group (including unrated), so this row would push the card's rated top-3 to
    # read 2/3/4 if the card rendered `rank` instead of the position within its rated set.
    ("lbhub-recon-unrated", "image_recon", 9500.0, 0),
    ("lbhub-recon-a", "image_recon", 9000.0, 40),  # firm (>= FIRM_VOTE_THRESHOLD)
    ("lbhub-recon-b", "image_recon", 8900.0, 5),
    ("lbhub-recon-c", "image_recon", 8800.0, 2),
    ("lbhub-recon-d", "image_recon", 8700.0, 1),  # 4th — must NOT appear in the card's top-3
    ("lbhub-text-a", "text_native", 8500.0, 3),  # provisional-only modality
    ("lbhub-retrieval", "retrieval", 9999.0, 50),  # app-hidden: must never surface
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
    """Drop the fixtures again. The suite shares one DB file, and these rows carry deliberately
    extreme BT scores — left behind, they would sit at the top of any later module's board and
    silently change the ranks those modules assert on."""
    with SessionLocal() as db:
        for slug, *_ in FIXTURES:
            g = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
            if g is None:
                continue
            db.execute(delete(Rating).where(Rating.generator_id == g.id))
            db.delete(g)
        db.commit()


# --------------------------------------------------------------- service.modality_hub_cards


def _fake_rows(paradigm: str) -> list[dict]:
    """Stands in for the route's closure over _leaderboard_rows/_finish."""
    data = {
        "image_recon": [
            {"generator": "A", "bt_score": 40.0, "n_games": 40, "rank": 1},
            {"generator": "B", "bt_score": 30.0, "n_games": 10, "rank": 2},
            {"generator": "C", "bt_score": 20.0, "n_games": 4, "rank": 3},
            {"generator": "D", "bt_score": 10.0, "n_games": 1, "rank": 4},
        ],
        "text_native": [{"generator": "T", "bt_score": 5.0, "n_games": 3, "rank": 1}],
        # unrated only -> no card (nothing has been voted on in this scope)
        "agentic": [{"generator": "Z", "bt_score": 0.0, "n_games": 0, "rank": 1}],
        "procedural_llm": [],
        # app-hidden paradigms must be skipped even if rows_fn would return rated rows
        "retrieval": [{"generator": "R", "bt_score": 99.0, "n_games": 99, "rank": 1}],
        "capture_scan": [{"generator": "S", "bt_score": 99.0, "n_games": 99, "rank": 1}],
        "procedural_expert": [{"generator": "E", "bt_score": 99.0, "n_games": 99, "rank": 1}],
    }
    return data.get(paradigm, [])


def test_modality_hub_cards_skips_hidden_and_unrated_and_follows_paradigm_order():
    cards = service.modality_hub_cards(_fake_rows)
    got = [c["paradigm"] for c in cards]
    assert got == ["image_recon", "text_native"], got
    for c in cards:
        assert c["paradigm"] not in config.APP_HIDDEN_PARADIGMS
    # order mirrors paradigms.PARADIGMS (image_recon precedes text_native there)
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    assert [order[p] for p in got] == sorted(order[p] for p in got)


def test_modality_hub_card_keys_and_values():
    card = service.modality_hub_cards(_fake_rows)[0]
    assert set(card) == {"paradigm", "display", "what", "top", "model_count", "firm"}
    assert card["display"] == paradigms.DISPLAY_NAMES["image_recon"]
    assert card["what"] == paradigms.WHAT_THIS_MEASURES["image_recon"]
    assert [r["generator"] for r in card["top"]] == ["A", "B", "C"]  # capped at 3
    assert card["model_count"] == 4  # all RATED entrants, not just the shown top-3
    assert card["firm"] is True  # A has 40 >= FIRM_VOTE_THRESHOLD


def test_modality_hub_card_provisional_when_no_row_hits_threshold():
    text = next(c for c in service.modality_hub_cards(_fake_rows) if c["paradigm"] == "text_native")
    assert text["firm"] is False
    assert text["model_count"] == 1
    assert service.FIRM_VOTE_THRESHOLD > 3  # guards the fixture's intent


# --------------------------------------------------------------------------- /leaderboard hub


def test_hub_shows_only_visible_modalities():
    html = client.get("/leaderboard").text
    assert 'class="lb-hub"' in html
    for hidden in ("Retrieved asset", "Expert / simulation procedural", "Scan / capture"):
        assert hidden not in html
    assert "lbhub-retrieval" not in html


def test_hub_card_links_to_modality_board():
    html = client.get("/leaderboard").text
    assert "/leaderboard/image_recon" in html
    assert "/leaderboard/retrieval" not in html


def test_hub_shows_top_models_and_population():
    html = client.get("/leaderboard").text
    assert "lbhub-recon-a" in html  # top-3 of the image_recon card
    assert "lbhub-recon-d" not in html  # 4th — not in the top-3 preview
    assert paradigms.WHAT_THIS_MEASURES["image_recon"] in html


def _card_ranks(html: str, paradigm: str) -> list[str]:
    """The rank numbers rendered inside one modality card (cards are anchors, in order)."""
    card = html.split(f'href="/leaderboard/{paradigm}')[1]
    return re.findall(r'lb-hub-rank mono">(\d+)<', card)


def test_hub_card_top3_starts_at_rank_one_despite_higher_unrated_prior():
    """lbhub-recon-unrated (n_games=0) carries the paradigm's highest prior BT, so the group-wide
    `rank` of the rated leader is 2 — the card must still read 1/2/3 for its RATED set."""
    html = client.get("/leaderboard").text
    assert "lbhub-recon-unrated" not in html  # unrated entrants never reach a card
    assert _card_ranks(html, "image_recon")[:3] == ["1", "2", "3"]


def test_hub_has_no_cross_paradigm_overall_ranking():
    html = client.get("/leaderboard").text
    assert "overall=true" not in html  # the Overall tab/board is gone
    assert "aren't comparable" in html.lower() or "not comparable" in html.lower()


def test_paradigm_query_still_renders_a_single_within_paradigm_board():
    html = client.get("/leaderboard?paradigm=image_recon").text
    assert 'class="lb-hub"' not in html
    assert paradigms.DISPLAY_NAMES["image_recon"] in html
    assert "lbhub-recon-a" in html
    assert "lbhub-text-a" not in html  # other modalities are not on this board
    assert "overall=true" not in html


def test_hidden_paradigm_board_is_empty():
    html = client.get("/leaderboard?paradigm=retrieval").text
    assert "lbhub-retrieval" not in html


# -------------------------------------------------- verified = a SCOPE MODIFIER, not a board


def _fake_verified_rows(*_a, **_kw) -> list[dict]:
    """A MERGED verified BT fit (what service.verified_leaderboard_rows returns): one ranking over
    every paradigm. Ranks land 1..4 across paradigms, so image_recon's leader sits at rank 3 —
    slicing this by paradigm (the old behavior) would show a board starting at rank 3."""

    def row(slug, paradigm, bt):
        return {
            "generator": slug,
            "kind": "model",
            "paradigm": paradigm,
            "generator_id": None,
            "slug": slug,
            "bt_score": bt,
            "bt_lower": bt - 5.0,
            "bt_upper": bt + 5.0,
            "n_games": 12,
        }

    return service.finalize_rows(
        [
            row("vtext-1", "text_native", 400.0),
            row("vtext-2", "text_native", 300.0),
            row("vrecon-1", "image_recon", 200.0),
            row("vrecon-2", "image_recon", 100.0),
        ]
    )


def test_verified_scope_renders_the_hub_not_a_merged_board(monkeypatch):
    monkeypatch.setattr(service, "verified_leaderboard_rows", _fake_verified_rows)
    r = client.get("/leaderboard?verified=true")
    assert r.status_code == 200
    assert 'class="lb-hub"' in r.text  # the SAME hub, cards built from the verified rows
    assert "Verified votes — all methods" not in r.text  # the merged board is gone
    # cards are per-paradigm and each starts at 1 (not a slice of the merged 1..4 ranking)
    assert _card_ranks(r.text, "image_recon")[:2] == ["1", "2"]
    assert "vrecon-1" in r.text and "vtext-1" in r.text


def test_verified_paradigm_board_is_a_fresh_within_paradigm_ranking(monkeypatch):
    monkeypatch.setattr(service, "verified_leaderboard_rows", _fake_verified_rows)
    html = client.get("/leaderboard?verified=true&paradigm=image_recon").text
    assert 'class="lb-hub"' not in html  # a single board, not the hub
    assert "vrecon-1" in html
    assert "vtext-1" not in html  # other paradigms are not on this board
    # Fresh 1..N: the BT cell is marked `strong` only for rank == 1. On a slice of the merged
    # ranking vrecon-1 would still carry rank 3 and nothing on the board would be rank 1.
    assert "num mono strong" in html
