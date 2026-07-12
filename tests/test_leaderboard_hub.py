"""/leaderboard is a modality HUB: one card per VISIBLE modality, each linking to that
modality's own board. The cross-paradigm "Overall" ranking is gone (paradigms are disconnected
match pools — a merged BT ordering was never a statistical claim). `?paradigm=X` renders a single
within-paradigm board; `?verified=true` is a SCOPE MODIFIER over the same two surfaces (hub /
one-paradigm board), never a merged cross-paradigm board of its own."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import config, main, paradigms, service
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
    # PRODUCTION SHAPE: on the real study DB only image_recon has any votes. These two modalities
    # are in the roster with ZERO votes — they must still get a (clickable, empty-state) hub card.
    ("lbhub-proc-unrated", "procedural_llm", 8400.0, 0),
    ("lbhub-agentic-unrated", "agentic", 8300.0, 0),
    ("lbhub-retrieval", "retrieval", 9999.0, 50),  # app-hidden: must never surface
]

# The modalities the hub is asked to render (what main._visible_modalities returns for the roster).
MODALITIES = ("agentic", "text_native", "image_recon", "procedural_llm")  # deliberately unordered


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


def _row(generator: str, bt: float, n_games: int) -> dict:
    """A leaderboard row as the route hands it to the hub: the WHOLE group, rated or not
    (modality_hub_cards owns the rated/unrated split and re-ranks the rated subset itself)."""
    return {
        "generator": generator,
        "bt_score": bt,
        "bt_lower": bt - 1.0,
        "bt_upper": bt + 1.0,
        "n_games": n_games,
    }


def _fake_rows(paradigm: str) -> list[dict]:
    """Stands in for the route's closure over _leaderboard_rows."""
    data = {
        "image_recon": [
            # UNRATED but highest prior BT: it must not take the card's rank-1 slot.
            _row("U", 50.0, 0),
            _row("A", 40.0, 40),  # firm
            _row("B", 30.0, 10),
            _row("C", 20.0, 4),
            _row("D", 10.0, 1),  # 4th rated — not in the top-3
        ],
        "text_native": [_row("T", 5.0, 3)],  # rated, but nothing near the firm threshold
        # UNRATED ONLY — the production shape: a modality nobody has voted on yet. It still has a
        # board, so it still gets a card (this used to be silently skipped).
        "agentic": [_row("Z", 0.0, 0)],
        "procedural_llm": [],  # in the roster, no rating rows at all in this scope
        # app-hidden paradigms must be skipped even if rows_fn would return rated rows
        "retrieval": [_row("R", 99.0, 99)],
        "capture_scan": [_row("S", 99.0, 99)],
        "procedural_expert": [_row("E", 99.0, 99)],
    }
    return data.get(paradigm, [])


def _cards(modalities=MODALITIES) -> list[dict]:
    return service.modality_hub_cards(_fake_rows, modalities)


def test_modality_hub_cards_skips_hidden_but_keeps_unrated_and_follows_paradigm_order():
    """Converted from the old `..._skips_hidden_and_unrated_...`: hidden paradigms are STILL
    skipped, but an unrated VISIBLE modality now keeps its card (it was silently dropped, which on
    production data — votes in image_recon only — rendered a one-card hub and orphaned 3 boards)."""
    got = [c["paradigm"] for c in _cards()]
    assert got == ["image_recon", "procedural_llm", "text_native", "agentic"], got
    for c in _cards():
        assert c["paradigm"] not in config.APP_HIDDEN_PARADIGMS
    # Order is paradigms.PARADIGMS', not the caller's (MODALITIES is deliberately unordered).
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    assert [order[p] for p in got] == sorted(order[p] for p in got)


def test_hidden_paradigm_never_gets_a_card_even_if_asked_for():
    for hidden in sorted(config.APP_HIDDEN_PARADIGMS):
        assert _cards(modalities=[hidden]) == [], hidden


def test_modality_hub_card_keys_and_values():
    card = _cards()[0]
    assert set(card) == {
        "paradigm",
        "display",
        "what",
        "top",
        "model_count",
        "rated_count",
        "firm_count",
        "firm",
    }
    assert card["display"] == paradigms.DISPLAY_NAMES["image_recon"]
    assert card["what"] == paradigms.WHAT_THIS_MEASURES["image_recon"]
    assert [r["generator"] for r in card["top"]] == ["A", "B", "C"]  # capped at 3; U is unrated
    assert [r["rank"] for r in card["top"]] == [1, 2, 3]  # re-ranked over the RATED set
    assert card["rated_count"] == 4  # A B C D
    assert card["model_count"] == 5  # + the unrated entrant U
    assert card["firm_count"] == 1  # only A has >= FIRM_VOTE_THRESHOLD games


def test_card_is_firm_only_when_every_rated_entrant_is_firm():
    """`any()` used to stamp the card "firm" the moment ONE model crossed the threshold — while the
    board it links to still showed most rows counting down ("N more votes → firm"). The card must
    not contradict its board."""
    card = _cards()[0]
    assert card["firm_count"] < card["rated_count"]  # 1 of 4
    assert card["firm"] is False
    all_firm = service.modality_hub_cards(
        lambda p: [_row("A", 40.0, service.FIRM_VOTE_THRESHOLD)], ["image_recon"]
    )[0]
    assert all_firm["firm"] is True and all_firm["firm_count"] == 1


def test_modality_hub_card_provisional_when_no_row_hits_threshold():
    text = next(c for c in _cards() if c["paradigm"] == "text_native")
    assert text["firm"] is False
    assert text["firm_count"] == 0
    assert text["rated_count"] == 1
    assert service.FIRM_VOTE_THRESHOLD > 3  # guards the fixture's intent


def test_unrated_modality_gets_an_empty_state_card_not_a_fake_rank():
    """The production shape: a modality with zero votes. Card exists (its board does), carries its
    model count, and claims NO ranking — no top-3, no rank, no firm pill."""
    agentic = next(c for c in _cards() if c["paradigm"] == "agentic")
    assert agentic["top"] == []
    assert agentic["rated_count"] == 0
    assert agentic["model_count"] == 1  # the entrant is disclosed, just not ranked
    assert agentic["firm"] is False and agentic["firm_count"] == 0
    # ...and a modality with no rating rows at all in scope still gets its card.
    proc = next(c for c in _cards() if c["paradigm"] == "procedural_llm")
    assert proc["model_count"] == 0 and proc["top"] == []


def test_hub_shows_every_visible_modality_when_only_one_has_votes():
    """The whole point: votes in ONE paradigm must not collapse the hub to one card."""
    cards = _cards()
    assert len(cards) == 4
    assert sum(1 for c in cards if c["rated_count"]) == 2  # image_recon + text_native
    assert [c["paradigm"] for c in cards if not c["rated_count"]] == ["procedural_llm", "agentic"]


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


def test_hub_links_every_roster_modality_including_the_unvoted_ones():
    """PRODUCTION SHAPE (this is what the study DB looks like): only image_recon has votes. Every
    modality in the roster must still be a card — /leaderboard/text_native & friends are real
    boards (/api/leaderboard publishes them), so the hub may not omit them."""
    with SessionLocal() as db:
        visible = main._visible_modalities(db)
    assert {"image_recon", "text_native", "procedural_llm", "agentic"} <= set(visible)
    html = client.get("/leaderboard").text
    for p in visible:
        assert f"/leaderboard/{p}?" in html, p  # a card, and a clickable one
    # the unvoted fixture modalities carry an honest empty state, never a rank
    assert "no votes yet — evaluation in progress" in html
    assert "lbhub-proc-unrated" not in html and "lbhub-agentic-unrated" not in html


def test_hub_firm_pill_does_not_contradict_the_board_it_links_to():
    """The image_recon fixture has ONE firm model (40 votes) and three below the threshold — the
    card used to read a bare "firm" while its board showed "N more votes → firm" on most rows."""
    html = client.get("/leaderboard").text
    card = html.split('href="/leaderboard/image_recon')[1].split("</a>")[0]
    assert re.search(r"\d+ of \d+ firm", card)  # a COUNT, matching what the board shows row by row
    assert ">all firm<" not in card  # ...and NOT the old any()-driven blanket claim


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


def _fake_zero_vote_board_rows(*_a, **_kw) -> list[dict]:
    """A board on which NOTHING has been voted on: identical priors, identical CIs, zero games —
    the shape every unvoted modality's board now has (they became reachable when the hub stopped
    skipping unrated modalities)."""

    def row(slug):
        return {
            "generator": slug,
            "kind": "model",
            "paradigm": "image_recon",
            "generator_id": None,
            "slug": slug,
            "bt_score": 1000.0,
            "bt_lower": 995.0,
            "bt_upper": 1005.0,
            "n_games": 0,
        }

    return service.finalize_rows([row("zv-1"), row("zv-2"), row("zv-3")])


def test_zero_vote_board_shares_rank_one_instead_of_inventing_an_order(monkeypatch):
    """The board printed `loop.index` under a "Rank" header: on a zero-vote board that reads
    1/2/3 for models with identical BT, identical CIs and not one comparison between them. It now
    prints the CI-GROUPED rank — all three share rank 1 — and no row wears a medal (a podium is a
    claim; there is no evidence for one here)."""
    monkeypatch.setattr(service, "verified_leaderboard_rows", _fake_zero_vote_board_rows)
    html = client.get("/leaderboard?verified=true&paradigm=image_recon").text
    assert re.findall(r'lb-rank-num mono">(\d+)<', html) == ["1", "1", "1"]
    assert "lb-medal" not in html


def test_verified_paradigm_board_is_a_fresh_within_paradigm_ranking(monkeypatch):
    monkeypatch.setattr(service, "verified_leaderboard_rows", _fake_verified_rows)
    html = client.get("/leaderboard?verified=true&paradigm=image_recon").text
    assert 'class="lb-hub"' not in html  # a single board, not the hub
    assert "vrecon-1" in html
    assert "vtext-1" not in html  # other paradigms are not on this board
    # Fresh 1..N: the BT cell is marked `strong` only for rank == 1. On a slice of the merged
    # ranking vrecon-1 would still carry rank 3 and nothing on the board would be rank 1.
    assert "num mono strong" in html


# ------------------------------------------------- GET /api/leaderboard: no cross-paradigm rank


def _by_paradigm(rows: list[dict]) -> dict[str | None, list[dict]]:
    out: dict[str | None, list[dict]] = {}
    for r in rows:
        out.setdefault(r.get("paradigm") or None, []).append(r)
    return out


def test_api_leaderboard_returns_per_paradigm_boards():
    """The JSON surface mirrors the HTML one: NO merged cross-paradigm ranking. With no
    `?paradigm=`, it returns one board per modality, each freshly ranked 1..N within itself."""
    body = client.get("/api/leaderboard").json()
    assert body["paradigm"] is None
    boards = {b["paradigm"]: b for b in body["boards"]}
    assert "image_recon" in boards and "text_native" in boards
    for b in body["boards"]:
        rows = b["rows"]
        assert rows, b["paradigm"]
        assert min(r["rank"] for r in rows) == 1  # a fresh within-paradigm 1..N, never a slice
        assert [r["bt_score"] for r in rows] == sorted((r["bt_score"] for r in rows), reverse=True)
        assert {r["paradigm"] or None for r in rows} == {b["paradigm"]}


def test_api_leaderboard_rank_is_never_cross_paradigm():
    """The defect this replaces: one merged BT ordering with a global rank 1..N over paradigms
    that never played each other. Now every rank is relative to its own modality — so, with >1
    modality on the board, there is more than one rank-1 row and no row's rank can be read as a
    cross-paradigm position."""
    rows = client.get("/api/leaderboard").json()["rows"]
    groups = _by_paradigm(rows)
    assert len(groups) > 1, "fixture should span several paradigms"
    for pgm, grows in groups.items():
        assert min(r["rank"] for r in grows) == 1, pgm
    # Every modality has its OWN rank-1 (CI-grouped ranks can tie several rows at 1 within a
    # board — the claim here is that rank 1 exists per modality, not once overall).
    leader_paradigms = {r.get("paradigm") or None for r in rows if r["rank"] == 1}
    assert leader_paradigms == set(groups)
    # ...and the flat list is grouped by modality, not merged BT-desc across them.
    top = groups["image_recon"][0]
    assert top["generator"] == "lbhub-recon-unrated"  # highest BT in ITS paradigm


def test_api_leaderboard_paradigm_param_returns_that_board_only():
    body = client.get("/api/leaderboard?paradigm=image_recon").json()
    assert body["paradigm"] == "image_recon"
    rows = body["rows"]
    assert {r["paradigm"] for r in rows} == {"image_recon"}
    assert rows[0]["rank"] == 1
    assert not any(r["generator"] == "lbhub-text-a" for r in rows)
    assert [b["paradigm"] for b in body["boards"]] == ["image_recon"]


def test_api_leaderboard_hidden_or_unknown_paradigm_404s():
    for hidden in sorted(config.APP_HIDDEN_PARADIGMS):
        assert client.get(f"/api/leaderboard?paradigm={hidden}").status_code == 404, hidden
    assert client.get("/api/leaderboard?paradigm=not-a-paradigm").status_code == 404


def test_api_leaderboard_never_exposes_an_app_hidden_paradigm():
    body = client.get("/api/leaderboard").json()
    seen = {b["paradigm"] for b in body["boards"]} | {r.get("paradigm") for r in body["rows"]}
    assert not (seen & config.APP_HIDDEN_PARADIGMS)
    assert not any(r["generator"] == "lbhub-retrieval" for r in body["rows"])


def test_api_leaderboard_verified_scope_is_also_within_paradigm(monkeypatch):
    """`?verified=true` used to bypass the paradigm filter entirely and hand back the merged
    verified BT fit (vrecon-1 at rank 3). It is a SCOPE modifier, so it gets the same treatment."""
    monkeypatch.setattr(service, "verified_leaderboard_rows", _fake_verified_rows)
    body = client.get("/api/leaderboard?verified=true&paradigm=image_recon").json()
    assert body["verified"] is True
    rows = body["rows"]
    assert [r["generator"] for r in rows] == ["vrecon-1", "vrecon-2"]
    assert [r["rank"] for r in rows] == [1, 2]  # fresh 1..N, not the merged 3..4


def test_api_leaderboard_verified_without_paradigm_has_no_merged_rank(monkeypatch):
    monkeypatch.setattr(service, "verified_leaderboard_rows", _fake_verified_rows)
    body = client.get("/api/leaderboard?verified=true").json()
    boards = {b["paradigm"]: b["rows"] for b in body["boards"]}
    assert set(boards) == {"image_recon", "text_native"}
    for pgm, rows in boards.items():
        assert [r["rank"] for r in rows] == [1, 2], pgm  # each modality starts at 1


def test_api_leaderboard_documents_the_within_paradigm_contract():
    body = client.get("/api/leaderboard").json()
    assert "paradigm" in body["note"].lower() or "modality" in body["note"].lower()


# --------------------------------------------------- regression guards for the whole redesign


def test_no_cross_paradigm_overall_board_can_be_resurrected():
    """The Overall board is GONE, not hidden behind a flag: an unknown `?overall=true` cannot
    bring it back (FastAPI ignores it) and the hub is what renders."""
    r = client.get("/leaderboard?overall=true")
    assert r.status_code == 200
    assert 'class="lb-hub"' in r.text  # the hub, not a board
    # The ranked BT table only exists on a single-modality board — never on the hub, in any
    # scope, under any query param.
    assert "lb-ranktable" not in r.text
    assert "lb-ranktable" not in client.get("/leaderboard?overall=true&verified=true").text
    assert "Overall — all methods" not in r.text


def test_app_hidden_paradigms_have_no_public_surface():
    for hidden in sorted(config.APP_HIDDEN_PARADIGMS):
        assert client.get(f"/leaderboard/{hidden}").status_code == 404, hidden
        assert client.get(f"/leaderboard/judge?modality={hidden}").status_code == 404, hidden
        assert client.get(f"/api/leaderboard?paradigm={hidden}").status_code == 404, hidden
    hub = client.get("/leaderboard").text
    for hidden in config.APP_HIDDEN_PARADIGMS:
        assert paradigms.DISPLAY_NAMES[hidden] not in hub
        assert f"/leaderboard/{hidden}" not in hub


# ------------------------------------------------------- presentation: hub grid + status pill


def _media_blocks(css: str) -> list[str]:
    """Every `@media ... { ... }` block, brace-balanced. A plain substring/regex scan can't tell a
    rule INSIDE a media block from one that merely follows it, and the assertion below is exactly
    'the hub grid is overridden inside a media query'."""
    blocks = []
    for m in re.finditer(r"@media[^{]*\{", css):
        depth = 0
        for j in range(m.end() - 1, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(css[m.start() : j + 1])
                    break
    return blocks


def test_hub_grid_is_styled_and_responsive():
    """The hub cards are a styled, responsive grid — not raw markup. (The card rules must live in
    the shipped stylesheet, so this reads the SERVED css, not the template.)"""
    css = client.get("/static/style.css").text
    assert ".lb-hub-grid" in css
    assert ".lb-hub-card" in css
    # collapses to a single column on narrow viewports
    assert any(".lb-hub-grid" in b for b in _media_blocks(css)), "no @media rule for the hub grid"


def test_hub_card_styling_uses_design_tokens_not_literal_colors():
    """The hub must read as a sibling of the existing card family (.b3d-model-card), which is
    built from the token vars — a hard-coded hex would drift the moment a theme changes."""
    css = client.get("/static/style.css").text
    card = css.split(".lb-hub-card")[1].split("}")[0]
    assert "var(--border)" in card and "var(--r-card)" in card
    assert "#" not in card  # no literal hex colors


def test_hub_renders_the_card_classes():
    html = client.get("/leaderboard").text
    for cls in ("lb-hub-grid", "lb-hub-card", "lb-hub-what", "lb-hub-top", "lb-hub-cta"):
        assert f'class="{cls}' in html or f'"{cls} ' in html or f' {cls}"' in html, cls


def test_board_status_cell_keeps_the_provisional_tooltip():
    """The per-row Status badge replaced an older `provisional` chip that carried a hover
    explanation ("Fewer than N votes — rank may still shift"). A not-yet-firm row must still
    explain itself on hover; a firm row needs no caveat."""
    html = client.get("/leaderboard?paradigm=image_recon").text
    m = re.search(r'<span class="cov-badge cov-prov"[^>]*title="([^"]+)"', html)
    assert m, "not-yet-firm status badge has no title tooltip"
    assert str(service.FIRM_VOTE_THRESHOLD) in m.group(1)
    assert "rank may still shift" in m.group(1)
    # the settled state is not a caveat — no "may still shift" wording on the firm badge
    assert re.search(r'<span class="cov-badge cov-firm">firm</span>', html)
