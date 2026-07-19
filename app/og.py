"""Branded Open Graph share cards (1200×630) — the drawing vocabulary, shared.

Two callers, one visual language:

  * ``scripts/gen_og_image.py`` bakes the SITE-DEFAULT card to ``app/static/og-default.png``;
  * ``GET /og/models/{slug}.png`` (app.main) renders a PER-MODEL card LIVE — a model's rank moves
    as votes land, so a baked asset would unfurl a stale standing.

Everything a card says about a model's standing goes through :func:`model_standing`, which is a
pure function and the single place the product's honesty invariants are enforced:

  * a rank is ALWAYS scoped to the model's own method. Every board on this site ranks exactly ONE
    paradigm (paradigms are disconnected match pools — see ``paradigms.same_paradigm`` and
    ``service._matches_for_scope``), so "#2 of 16 in Image→3D reconstruction" is a claim the
    ranking math backs and a bare "#2 in Bio 3D Arena" is not;
  * under ``service.FIRM_VOTE_THRESHOLD`` votes a model reads as evaluation-in-progress
    (provisional + the vote count), never as a settled rank;
  * with no votes there is no rank to state, so none is drawn.

Pure PIL + the v2 design tokens; no DB, no app imports (the caller passes the facts in), so the
script can import it without standing up an engine.
"""

from __future__ import annotations

import glob
import io

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
MARGIN = 72

# v2 design tokens, sampled to sRGB (the CSS is OKLCH; PIL is not).
BG = (18, 22, 31)  # ~ --bg  oklch(0.165 0.018 258)
PANEL = (27, 32, 43)  # ~ --panel
BORDER = (46, 54, 70)  # ~ --border
ACCENT = (70, 201, 138)  # ~ --accent (green)
TEXT = (232, 236, 242)
MUTED = (150, 160, 176)

_FONTS: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}


def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    """DejaVu at `size` (cached). Resolved from matplotlib's bundled copy or the system —
    the runtime never ships a font of its own."""
    key = (bold, size)
    hit = _FONTS.get(key)
    if hit is not None:
        return hit
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    resolved: ImageFont.FreeTypeFont | None = None
    for pat in (
        f"/home/mjarnold/miniconda3/lib/python3.13/site-packages/matplotlib/mpl-data/fonts/ttf/{name}",
        f"/usr/share/fonts/truetype/dejavu/{name}",
        f"/usr/share/fonts/**/{name}",
    ):
        hits = glob.glob(pat, recursive=True)
        if hits:
            resolved = ImageFont.truetype(hits[0], size)
            break
    if resolved is None:
        resolved = ImageFont.load_default()
    _FONTS[key] = resolved
    return resolved


def new_card() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """A blank card on the dark canvas with the accent rule across the top."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=ACCENT)
    return img, d


def draw_wordmark(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """The eyebrow: accent dot + BIO 3D ARENA. Same mark on every card."""
    d.ellipse([x, y + 6, x + 22, y + 28], fill=ACCENT)
    d.text((x + 36, y), "BIO 3D ARENA", font=font(True, 30), fill=TEXT)


def fit_font(
    d: ImageDraw.ImageDraw, text: str, bold: bool, max_size: int, min_size: int, max_width: int
) -> ImageFont.FreeTypeFont:
    """Largest size at/below `max_size` that keeps `text` inside `max_width` (floor `min_size`)."""
    size = max_size
    while size > min_size and d.textlength(text, font=font(bold, size)) > max_width:
        size -= 2
    return font(bold, size)


def _truncate(d: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, max_width: int) -> str:
    if d.textlength(text, font=f) <= max_width:
        return text
    while text and d.textlength(text + "…", font=f) > max_width:
        text = text[:-1]
    return text + "…"


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- the honesty logic


def model_standing(
    *,
    modality: str,
    rank: int | None,
    rank_of: int | None,
    bt_score: float | None,
    votes: int,
    firm: bool,
    firm_label: str,
) -> dict:
    """What this model's standing may truthfully be said to be, for BOTH the card and the meta
    description (one function so the image and the text can never drift apart).

    `firm` / `firm_label` come from ``service.firm_status(votes)`` — passed in, not looked up, so
    this module stays DB-free and the threshold lives in exactly one place.
    """
    if votes <= 0 or rank is None or bt_score is None:
        # No votes → no rank exists. Say so; never dress the default prior up as a standing.
        return {
            "rated": False,
            "firm": False,
            "rank_text": None,
            "headline": "Not yet ranked",
            "sub": f"No blind votes yet in {modality} — evaluation in progress",
            "chip": "AWAITING VOTES",
            "scope": f"in {modality}",
        }
    rank_text = f"#{rank} of {rank_of}" if rank_of else f"#{rank}"
    if firm:
        sub = f"firm · {votes} blind votes"
    else:
        # Thin evidence: the rank is a snapshot, not a settled result. Both facts are stated.
        sub = f"provisional · {votes} {'vote' if votes == 1 else 'votes'} · {firm_label}"
    return {
        "rated": True,
        "firm": firm,
        "rank_text": rank_text,
        "headline": f"{rank_text} in {modality}",
        "sub": sub,
        "chip": "FIRM" if firm else "PROVISIONAL",
        "scope": f"in {modality}",
    }


def share_description(
    *,
    name: str,
    modality: str,
    standing: dict,
    bt_score: float | None,
    votes: int,
    site_name: str = "Bio 3D Arena",
) -> str:
    """The one-line og:description for a model page. Mirrors the card exactly (same `standing`)."""
    if not standing["rated"]:
        return (
            f"{name} — {modality} on {site_name}. Not yet rated: no blind votes yet, so no rank. "
            f"Vote in the arena to help place it."
        )
    hedge = "" if standing["firm"] else " (provisional — evaluation in progress)"
    return (
        f"{name} — {standing['rank_text']} {standing['scope']} on {site_name}{hedge}. "
        f"Bradley–Terry {bt_score:.0f} from {votes} blind human votes, ranked within its own "
        f"method (scores from different methods aren't comparable)."
    )


# --------------------------------------------------------------------------- the cards


def render_model_card(
    *,
    name: str,
    modality: str,
    bt_score: float | None,
    rank: int | None,
    rank_of: int | None,
    votes: int,
    firm: bool,
    firm_label: str,
) -> bytes:
    """The per-model share card, as PNG bytes. Every number on it is scoped to `modality`."""
    st = model_standing(
        modality=modality,
        rank=rank,
        rank_of=rank_of,
        bt_score=bt_score,
        votes=votes,
        firm=firm,
        firm_label=firm_label,
    )
    img, d = new_card()
    m = MARGIN
    inner = W - 2 * m

    draw_wordmark(d, m, 56)

    # Model name — the headline. Shrinks to fit, then truncates rather than overflow the card.
    nf = fit_font(d, name, True, 76, 40, inner)
    d.text((m, 122), _truncate(d, name, nf, inner), font=nf, fill=TEXT)

    # The method this model competes in. It is not decoration: it is the SCOPE of every number
    # below, so it is drawn as prominently as the rank.
    chip_txt = st["chip"]
    chip_font = font(True, 22)
    chip_w = int(d.textlength(chip_txt, font=chip_font)) + 36
    chip_x1, chip_y0 = W - m, 232
    chip_col = ACCENT if st["firm"] else MUTED
    d.rounded_rectangle(
        [chip_x1 - chip_w, chip_y0, chip_x1, chip_y0 + 40], radius=20, outline=chip_col, width=2
    )
    d.text((chip_x1 - chip_w + 18, chip_y0 + 8), chip_txt, font=chip_font, fill=chip_col)

    mf = fit_font(d, modality, True, 34, 22, inner - chip_w - 24)
    d.text((m, 236), _truncate(d, modality, mf, inner - chip_w - 24), font=mf, fill=ACCENT)

    # Stats panel
    py0, py1 = 310, 500
    d.rounded_rectangle([m, py0, W - m, py1], radius=18, fill=PANEL, outline=BORDER, width=2)
    px = m + 40
    if not st["rated"]:
        d.text((px, py0 + 46), "Not yet ranked", font=font(True, 46), fill=MUTED)
        d.text(
            (px, py0 + 116),
            "No blind votes yet — this model is still being evaluated.",
            font=font(False, 26),
            fill=MUTED,
        )
    else:
        # Rank reads in the accent ONLY when it is firm; a provisional rank is deliberately muted
        # so the card never *looks* like a settled result even at a glance.
        rank_col = ACCENT if st["firm"] else MUTED
        rf = font(True, 68)
        d.text((px, py0 + 38), f"#{rank}", font=rf, fill=rank_col)
        rw = int(d.textlength(f"#{rank}", font=rf))
        if rank_of:
            d.text((px + rw + 12, py0 + 68), f"of {rank_of}", font=font(False, 30), fill=MUTED)
        d.text((px, py0 + 128), "rank in this method", font=font(False, 24), fill=MUTED)

        col2 = m + 440
        d.text((col2, py0 + 48), f"{bt_score:.0f}", font=font(True, 52), fill=TEXT)
        d.text((col2, py0 + 128), "BT score (this method)", font=font(False, 24), fill=MUTED)

        col3 = m + 760
        d.text((col3, py0 + 48), f"{votes}", font=font(True, 52), fill=TEXT)
        d.text((col3, py0 + 128), "blind human votes", font=font(False, 24), fill=MUTED)

    # Evidence line (firm / provisional + countdown) then the invariant, spelled out.
    d.text((m, 516), st["sub"], font=font(False, 26), fill=MUTED)
    d.text(
        (m, 562),
        "Ranked within its own method · scores across methods aren't comparable",
        font=font(True, 24),
        fill=ACCENT,
    )
    return to_png(img)


def render_leaderboard_card(
    *,
    scope_label: str,
    n_models: int,
    n_methods: int,
    votes: int,
) -> bytes:
    """The leaderboard share card, as PNG bytes.

    Deliberately states NO cross-method ranking. Every board ranks exactly ONE paradigm
    (paradigms are disconnected match pools), so a single "top model of the leaderboard" would be
    a claim the ranking math does not back — the same invariant `model_standing` enforces. The
    card shows the board's SCOPE and size, the vote count, and points back at the arena.
    """
    img, d = new_card()
    m = MARGIN
    inner = W - 2 * m

    draw_wordmark(d, m, 56)

    head = f"{scope_label} leaderboard"
    hf = fit_font(d, head, True, 72, 40, inner)
    d.text((m, 122), _truncate(d, head, hf, inner), font=hf, fill=TEXT)

    d.text(
        (m, 214),
        "Bradley–Terry rankings from blind human votes",
        font=font(False, 30),
        fill=MUTED,
    )

    py0, py1 = 300, 494
    d.rounded_rectangle([m, py0, W - m, py1], radius=18, fill=PANEL, outline=BORDER, width=2)
    stats = [
        (str(n_models), "models ranked"),
        (str(n_methods), "generation method" + ("" if n_methods == 1 else "s")),
        (f"{votes:,}", "blind human votes"),
    ]
    step = (W - 2 * m - 80) // 3
    for i, (big, label) in enumerate(stats):
        x = m + 40 + i * step
        d.text((x, py0 + 44), big, font=font(True, 60), fill=ACCENT if i == 0 else TEXT)
        d.text((x, py0 + 130), label, font=font(False, 24), fill=MUTED)

    d.text(
        (m, 520),
        "Ranked within each method · scores across methods aren't comparable",
        font=font(True, 24),
        fill=ACCENT,
    )
    return to_png(img)


def render_default_card() -> Image.Image:
    """The site-default card (`/static/og-default.png`) — kept here so both cards share one
    canvas, one palette and one wordmark."""
    img, d = new_card()
    m = 84
    draw_wordmark(d, m, m)
    d.text((m, m + 96), "Which model rebuilds", font=font(True, 78), fill=TEXT)
    d.text((m, m + 188), "life best?", font=font(True, 78), fill=ACCENT)
    d.text(
        (m, m + 306),
        "A blind benchmark arena for 3D generative models",
        font=font(False, 34),
        fill=MUTED,
    )
    d.text((m, m + 352), "of real organisms.", font=font(False, 34), fill=MUTED)
    d.text((m, H - m - 8), "Plants   ·   Fungi   ·   Animals", font=font(True, 30), fill=ACCENT)
    return img
