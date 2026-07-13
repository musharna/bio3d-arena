"""TDD for shareable result cards (task #75): a per-model Open Graph image + a Share affordance.

A shared model link must unfurl into a branded card that states the model's standing HONESTLY:

  * the rank is WITHIN ITS OWN METHOD (paradigms are disconnected match pools — a bare
    "#2 in Bio 3D Arena" would be a cross-paradigm claim the ranking math does not back);
  * a model under `service.FIRM_VOTE_THRESHOLD` votes reads as evaluation-in-progress
    (provisional), never as a settled rank;
  * an unrated model gets no rank at all;
  * an app-hidden generator is never leaked (404, like /models/{slug}).

The image is a LIVE route (ranks move as votes land) with an in-process byte cache, not a baked
asset.
"""

from __future__ import annotations

import io
import re

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app import config, main, og, paradigms, service
from app.database import SessionLocal
from app.main import app
from app.models import Category, Criterion, Generator, ModelOutput, Rating, Task
from app.seed import seed_all

client = TestClient(app)

# The suite shares ONE temp DB: every fixture row carries a `shr-` prefix (Generator.slug is
# UNIQUE), and the models live in the `sketch` modality — a paradigm no other module ranks — so a
# rated fixture here can never reorder another module's board.
PFX = "shr"
PARADIGM = "sketch"
MODALITY = paradigms.DISPLAY_NAMES[PARADIGM]

FIRM_VOTES = service.FIRM_VOTE_THRESHOLD + 10  # comfortably firm
PROV_VOTES = 12  # under the threshold → provisional


def _meta(html: str, prop: str, attr: str = "property") -> str:
    m = re.search(rf'<meta\s+{attr}="{re.escape(prop)}"\s+content="([^"]*)"', html)
    return m.group(1) if m else ""


def setup_module(_module):
    seed_all(force=True)
    with SessionLocal() as db:
        crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        cat = Category(slug=f"{PFX}-cat", name="Shr Cat")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title=f"{PFX}-task", prompt="p")
        db.add(task)
        db.flush()

        spec = [
            ("firm", "ShrFirmModel", 1180.0, FIRM_VOTES),
            ("prov", "ShrProvModel", 1050.0, PROV_VOTES),
            ("unrated", "ShrUnratedModel", None, 0),
        ]
        for slug, name, bt, votes in spec:
            g = Generator(slug=f"{PFX}-{slug}", name=name, kind="model", paradigm=PARADIGM)
            db.add(g)
            db.flush()
            db.add(
                ModelOutput(
                    task_id=task.id,
                    generator_id=g.id,
                    asset_path=f"{PFX}-{slug}.glb",
                    asset_format="glb",
                    n_comparisons=votes,
                )
            )
            if bt is not None:
                db.add(
                    Rating(
                        generator_id=g.id,
                        category_id=None,
                        criterion_id=crit.id,
                        elo=1000.0,
                        bt_score=bt,
                        bt_lower=bt - 20.0,
                        bt_upper=bt + 20.0,
                        n_games=votes,
                    )
                )
        # An app-hidden generator (AgriGen's internal testers) must 404 on the OG route too.
        if (
            db.execute(select(Generator).where(Generator.slug == "agrigen")).scalars().first()
            is None
        ):
            db.add(Generator(slug="agrigen", name="AgriGen", kind="model", paradigm="image_recon"))
        db.commit()


def teardown_module(_module):
    """Drop the fixture rows: a rated generator left behind would show up on later modules'
    boards (the suite shares one DB file)."""
    with SessionLocal() as db:
        for slug in (f"{PFX}-firm", f"{PFX}-prov", f"{PFX}-unrated"):
            g = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
            if g is None:
                continue
            for r in db.execute(select(Rating).where(Rating.generator_id == g.id)).scalars().all():
                db.delete(r)
            for o in (
                db.execute(select(ModelOutput).where(ModelOutput.generator_id == g.id))
                .scalars()
                .all()
            ):
                db.delete(o)
            db.delete(g)
        db.commit()
    main._OG_CARD_CACHE.clear()


# ------------------------------------------------------------------ the live OG image route


def test_og_route_renders_a_real_png_for_a_rated_model():
    r = client.get(f"/og/models/{PFX}-firm.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert len(r.content) > 5000  # a real card, not a stub
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"
    assert img.size == (og.W, og.H) == (1200, 630)


def test_og_route_404s_for_unknown_slug():
    assert client.get("/og/models/no-such-generator-slug.png").status_code == 404


def test_og_route_404s_for_app_hidden_generator():
    """App-hidden testers are 404 on /models/{slug}; the OG route must not leak them either."""
    assert "agrigen" in config.APP_HIDDEN_GENERATOR_SLUGS
    assert client.get("/models/agrigen").status_code == 404
    assert client.get("/og/models/agrigen.png").status_code == 404


def test_og_route_caches_bytes_across_unfurls():
    """Repeated unfurls must not redraw: the bytes are cached in-process against the rating +
    vote version, so the second hit is byte-identical and served from the cache."""
    main._OG_CARD_CACHE.clear()
    first = client.get(f"/og/models/{PFX}-firm.png")
    assert f"{PFX}-firm" in main._OG_CARD_CACHE
    second = client.get(f"/og/models/{PFX}-firm.png")
    assert first.content == second.content


# ------------------------------------------------------------------ the meta tags on the page


def test_model_page_og_image_points_at_its_own_card():
    html = client.get(f"/models/{PFX}-firm").text
    og_image = _meta(html, "og:image")
    assert og_image == main._abs_url(f"/og/models/{PFX}-firm.png")
    assert og_image != main._abs_url(config.OG_IMAGE_PATH)  # NOT the generic site card
    assert _meta(html, "twitter:image", attr="name") == og_image


def test_model_page_og_title_names_the_model_and_its_method():
    html = client.get(f"/models/{PFX}-firm").text
    title = _meta(html, "og:title")
    assert "ShrFirmModel" in title
    assert MODALITY in title


def test_share_description_states_the_rank_within_its_method():
    desc = _meta(client.get(f"/models/{PFX}-firm").text, "description", attr="name")
    assert "ShrFirmModel" in desc
    assert "#1" in desc
    assert MODALITY in desc  # the rank is scoped to the method — never a bare site-wide "#1"
    assert "within its own method" in desc


def test_unrated_model_claims_no_rank_anywhere():
    html = client.get(f"/models/{PFX}-unrated").text
    desc = _meta(html, "description", attr="name")
    assert "#" not in desc  # no rank may be manufactured from zero votes
    assert "not yet rated" in desc.lower()
    r = client.get(f"/og/models/{PFX}-unrated.png")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")


def test_provisional_model_is_labelled_provisional_not_settled():
    desc = _meta(client.get(f"/models/{PFX}-prov").text, "description", attr="name")
    assert "provisional" in desc.lower()
    assert str(PROV_VOTES) in desc  # the thin evidence is stated, not hidden
    with SessionLocal() as db:
        gen = db.execute(select(Generator).where(Generator.slug == f"{PFX}-prov")).scalars().first()
        ctx = main._model_share_context(db, gen)
    assert ctx["standing"]["firm"] is False
    assert ctx["standing"]["chip"] == "PROVISIONAL"
    assert ctx["standing"]["rated"] is True


# ------------------------------------------------------------------ the honesty logic (pure)


def test_model_standing_scopes_every_rank_to_its_method():
    st = og.model_standing(
        modality="Image→3D reconstruction",
        rank=2,
        rank_of=16,
        bt_score=1120.0,
        votes=84,
        firm=True,
        firm_label="firm",
    )
    assert st["rated"] is True and st["firm"] is True
    assert st["rank_text"] == "#2 of 16"
    assert st["headline"] == "#2 of 16 in Image→3D reconstruction"
    assert st["chip"] == "FIRM"


def test_model_standing_refuses_a_rank_without_votes():
    st = og.model_standing(
        modality="Sketch→3D",
        rank=None,
        rank_of=0,
        bt_score=None,
        votes=0,
        firm=False,
        firm_label="30 more votes → firm",
    )
    assert st["rated"] is False
    assert st["rank_text"] is None
    assert "#" not in st["headline"]
    assert "not yet" in st["headline"].lower()


def test_model_standing_marks_thin_evidence_provisional():
    status = service.firm_status(12)
    st = og.model_standing(
        modality="Sketch→3D",
        rank=1,
        rank_of=2,
        bt_score=1050.0,
        votes=12,
        firm=status["firm"],
        firm_label=status["label"],
    )
    assert st["firm"] is False
    assert st["chip"] == "PROVISIONAL"
    assert "provisional" in st["sub"].lower() and "12" in st["sub"]


def test_render_model_card_produces_a_valid_1200x630_png():
    png = og.render_model_card(
        name="ShrFirmModel",
        modality=MODALITY,
        bt_score=1180.0,
        rank=1,
        rank_of=2,
        votes=FIRM_VOTES,
        firm=True,
        firm_label="firm",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == (1200, 630)
    # The card is drawn on the v2 dark canvas with the green accent rule — same visual language
    # as the default card (scripts/gen_og_image.py), which now shares these helpers.
    assert img.getpixel((600, 2)) == og.ACCENT
    assert img.getpixel((5, 620)) == og.BG


# ------------------------------------------------------------------ the Share affordance


def test_model_page_renders_a_share_control():
    html = client.get(f"/models/{PFX}-firm").text
    assert 'class="b3d-share"' in html
    assert "Copy link" in html
    assert f"/og/models/{PFX}-firm.png" in html  # its own card is wired into the meta
    assert main._abs_url(f"/models/{PFX}-firm") in html  # the link the copy button copies
    assert "x.com/intent" in html  # X/Twitter share intent
    assert "share.js" in html


def test_share_styles_use_design_tokens_not_hard_coded_colors():
    css = (main.APP_DIR / "static" / "style.css").read_text()
    block = css.split("/* === Share (task #75)")[1].split("/* ===")[0]
    assert "var(--" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)  # no hard-coded hex: both themes must work


def test_arena_reveal_has_no_share_control():
    """The post-vote reveal was deliberately decluttered (#60) — the share affordance lives on the
    model page, and must not creep back into the arena."""
    html = client.get("/arena").text
    assert 'class="b3d-share"' not in html
