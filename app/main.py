"""FastAPI application — arena, voting, leaderboard, tasks, and admin tools."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import (
    config,
    dataset,
    difficulty,
    fidelity,
    ingest,
    integrity,
    kingdoms,
    matchmaking,
    og,
    paradigms,
    ranking,
    service,
    submissions,
    variants,
)
from .database import get_db, init_db
from .models import (
    CalibrationPair,
    Category,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    KBallot,
    ModelOutput,
    OutputFlag,
    Rating,
    Task,
    User,
    Vote,
    VoterSession,
)
from .models import _utcnow as _models_utcnow
from .schemas import CategoryIn, FlagIn, GeneratorIn, KVoteIn, TaskIn, VoteIn
from .storage import content_type_for, get_storage

logger = logging.getLogger(__name__)

config.ensure_dirs()
init_db()

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

_STATIC_DIR = APP_DIR / "static"


def _asset_url(path: str) -> str:
    """Cache-busting static URL. Appends the file's mtime as ?v= so a changed
    asset gets a fresh URL — this stops a browser from pairing freshly-deployed
    HTML with a stale cached JS/CSS (which throws null-element errors when the
    markup and script drift apart)."""
    rel = path.lstrip("/")
    try:
        version = int((_STATIC_DIR / rel).stat().st_mtime)
    except OSError:
        return f"/static/{rel}"
    return f"/static/{rel}?v={version}"


def _abs_url(path: str) -> str:
    """Absolute URL for Open Graph tags (og:url / og:image require an absolute URL). Joins the
    request path onto config.PUBLIC_BASE_URL (set per deploy)."""
    return f"{config.PUBLIC_BASE_URL}/{path.lstrip('/')}"


templates.env.globals["asset"] = _asset_url
templates.env.globals["abs_url"] = _abs_url
# Votes below which a generator's rank is flagged "provisional" — available to every template
# (the leaderboard route also passes it in context, which harmlessly shadows this global).
templates.env.globals["firm_vote_threshold"] = service.FIRM_VOTE_THRESHOLD
templates.env.globals["site_name"] = config.SITE_NAME
templates.env.globals["site_tagline"] = config.SITE_TAGLINE
templates.env.globals["og_image_path"] = config.OG_IMAGE_PATH
# Read live (not the value at import) so tests/deploys can toggle config.INTERNAL_PAGES_ENABLED
# and both the route guard and the nav conditionals see the same current value.
templates.env.globals["internal_pages"] = lambda: config.INTERNAL_PAGES_ENABLED
# Same live-read reason as above. Returns a dict rather than two globals so a template can
# never render the widget while missing the key it needs — the two travel together.
templates.env.globals["captcha"] = lambda: {
    "enabled": bool(config.REQUIRE_CAPTCHA),
    "provider": config.CAPTCHA_PROVIDER,
    "site_key": config.CAPTCHA_SITE_KEY,
}

app = FastAPI(title="Bio 3D Arena", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
# Local backend serves assets from disk; the S3 backend serves them from the bucket/CDN.
storage = get_storage()
if not storage.remote:
    app.mount("/assets", StaticFiles(directory=str(config.ASSET_DIR)), name="assets")

SESSION_COOKIE = "bio3d_session"


def _client_ip(request: Request) -> str:
    """Resolve the client IP for per-IP rate limiting. X-Forwarded-For is trusted ONLY behind a
    known proxy (config.TRUST_FORWARDED_FOR) — an untrusted client can spoof the header to dodge
    the limit; otherwise the socket peer address is authoritative."""
    if config.TRUST_FORWARDED_FOR:
        xff = request.headers.get("x-forwarded-for", "")
        if xff.strip():
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def ensure_session(request: Request, call_next):
    """Attach an anonymous session id (cookie) used for light dedup + history."""
    sid = request.cookies.get(SESSION_COOKIE)
    is_new = sid is None
    if is_new:
        sid = uuid.uuid4().hex
    request.state.session_id = sid
    request.state.client_ip = _client_ip(request)
    from . import auth

    request.state.login_enabled = auth._login_enabled()
    # Resolve the verified user (if any) for templates — one light lookup per request.
    request.state.user = None
    try:
        from .database import SessionLocal
        from .models import User, VoterSession

        with SessionLocal() as _db:
            _vs = _db.get(VoterSession, sid)
            if _vs is not None and _vs.user_id is not None:
                request.state.user = _db.get(User, _vs.user_id)
    except Exception:  # noqa: BLE001 — never let user-resolution break a page
        request.state.user = None
    _kq = request.query_params.get("kingdom")
    _kingdom = kingdoms.normalize_kingdom(
        _kq if _kq is not None else request.cookies.get("bio3d_kingdom")
    )
    request.state.kingdom = _kingdom
    # Kingdom-scoped stats strip (`.b3d-kstats`) — HTML pages only, never /api (matchmaking's
    # /api/next must stay fast) or static/asset/health/auth routes. try/except + None default
    # so a stats failure can never 500 a page.
    request.state.kingdom_stats = None
    if not request.url.path.startswith(("/api", "/static", "/assets", "/healthz", "/auth")):
        try:
            from .database import SessionLocal

            with SessionLocal() as _stats_db:
                request.state.kingdom_stats = service.kingdom_scope_stats(_stats_db, _kingdom)
        except Exception:  # noqa: BLE001 — never let stats computation break a page
            request.state.kingdom_stats = None
    response = await call_next(request)
    if is_new:
        response.set_cookie(
            SESSION_COOKIE,
            sid,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 365,
            secure=config.COOKIE_SECURE,
        )
    if _kq is not None:
        response.set_cookie(
            "bio3d_kingdom",
            _kingdom,
            max_age=60 * 60 * 24 * 365,
            samesite="lax",
        )
    return response


OAUTH_STATE_COOKIE = "bio3d_oauth_state"


@app.get("/auth/login")
def auth_login(request: Request):
    from . import auth

    if not auth._login_enabled():
        return RedirectResponse("/", status_code=302)
    state = auth.new_state()
    redirect_uri = f"{config.PUBLIC_BASE_URL}/auth/callback"
    resp = RedirectResponse(auth.authorize_url(state, redirect_uri), status_code=302)
    resp.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
    )
    return resp


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)):
    from . import auth

    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not auth._login_enabled() or not code or not state or state != cookie_state:
        resp = RedirectResponse("/?login=error", status_code=302)
        resp.delete_cookie(OAUTH_STATE_COOKIE)
        return resp
    try:
        redirect_uri = f"{config.PUBLIC_BASE_URL}/auth/callback"
        token = auth.exchange_code(code, redirect_uri)
        info = auth.fetch_userinfo(token)
    except auth.AuthError:
        resp = RedirectResponse("/?login=error", status_code=302)
        resp.delete_cookie(OAUTH_STATE_COOKIE)
        return resp
    user = db.execute(select(User).where(User.hf_id == info["hf_id"])).scalars().first()
    if user is None:
        user = User(hf_id=info["hf_id"], username=info["username"])
        db.add(user)
        db.flush()
    else:
        user.username = info["username"]
    vs = integrity.get_or_create_session(db, request.state.session_id)
    vs.user_id = user.id
    db.commit()
    resp = RedirectResponse("/?login=ok", status_code=302)
    resp.delete_cookie(OAUTH_STATE_COOKIE)
    return resp


@app.post("/auth/logout")
def auth_logout(request: Request, db: Session = Depends(get_db)):
    vs = db.get(VoterSession, request.state.session_id)
    if vs is not None:
        vs.user_id = None
        db.commit()
    return RedirectResponse("/", status_code=302)


# --------------------------------------------------------------------- helpers


def _default_criterion(db: Session) -> Criterion:
    crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
    if crit is None:
        raise HTTPException(500, "No 'overall' criterion — run the seed.")
    return crit


def _resolve_category_id(db: Session, category_slug: str | None) -> int | None:
    if not category_slug or category_slug == "all":
        return None
    cat = db.execute(select(Category).where(Category.slug == category_slug)).scalars().first()
    return cat.id if cat else None


def _effective_category_ids(k_ids: set[int] | None, category_id: int | None) -> set[int] | None:
    """Intersect an explicit `?category=` selector with the active kingdom's category set (a
    chosen category is always within a kingdom in normal use — /api/meta only ever offers
    in-kingdom categories — but this keeps the pool correct even for a stale/out-of-kingdom
    selector). None means 'no restriction'; pick_task's `category_ids` kwarg takes precedence
    over its `category_id` kwarg, so a single combined set is what must be passed."""
    if k_ids is None:
        return {category_id} if category_id is not None else None
    if category_id is None:
        return k_ids
    return {category_id} if category_id in k_ids else set()


def _kingdom_is_live(db: Session, kingdom: str) -> bool:
    """True when `kingdom` has >=1 active Task in its mapped categories. `all` (no scoping)
    is always live. A kingdom whose categories exist but have zero tasks yet (e.g. Animals —
    seeded as a category placeholder, self-activates the moment its first task is added, same
    convention as the `coming_soon` category flag in /api/meta) is NOT live — the data pages
    route to the roadmap screen instead of rendering an empty board."""
    kingdom = kingdoms.normalize_kingdom(kingdom)
    if kingdom == "all":
        return True
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    if not k_ids:
        return False
    return (
        db.execute(select(Task.id).where(Task.category_id.in_(k_ids), Task.active.is_(True)))
        .scalars()
        .first()
        is not None
    )


def _roadmap_or_none(request: Request, db: Session) -> HTMLResponse | None:
    """Return the coming-soon roadmap page when the request's active kingdom isn't live yet;
    else None so the caller renders its normal template. Applied at the top of every
    kingdom-scoped data route (Leaderboard, Arena, Difficulty, Significance, Benchmark,
    Coverage, Tasks, Dataset) — Home/Methodology/Submit/Spotlight are never gated."""
    kingdom = request.state.kingdom
    if kingdom == "all" or _kingdom_is_live(db, kingdom):
        return None
    return templates.TemplateResponse(
        request,
        "_kingdom_roadmap.html",
        {
            "kingdom": kingdom,
            "kingdom_label": kingdoms.KINGDOM_LABEL.get(kingdom, kingdom.title()),
        },
    )


def _arena_asset_url(o: ModelOutput) -> str:
    """Opaque, output-scoped asset URL for the ANONYMIZED arena payloads. The raw asset_path can
    encode gold status or generator identity (e.g. `gold/task19__bad.glb`), which a voter could
    read in devtools to unmask the planted-bad decoy or the model — so the arena never exposes it;
    the /media/o/{id} route (below) resolves the id back to the file. output_id is already in the
    payload (K-wise picks reference it), so this leaks nothing new. The extension is cosmetic."""
    return f"/media/o/{o.id}.{o.asset_format}"


def _serialize(
    comparison: Comparison,
    task: Task,
    crit: Criterion,
    out_a: ModelOutput,
    out_b: ModelOutput,
    references: list[dict] | None = None,
) -> dict:
    """Anonymized arena payload — never leaks generator identity or gold status. `references` is
    the subject's reference gallery (input photo + CC species photos — what the organism should
    look like), shown so voters can judge fidelity — not identity-revealing (shared across both
    candidates)."""
    from .public_export import is_commercial_model

    return {
        "comparison_id": comparison.id,
        "task": {
            "title": task.title,
            "prompt": task.prompt,
            "category": task.category.name,
            "references": references or [],
        },
        "criterion": {"slug": crit.slug, "name": crit.name},
        "a": {
            "url": _arena_asset_url(out_a),
            "format": out_a.asset_format,
            "output_id": out_a.id,
            "machine_generated": is_commercial_model(out_a.source),
            "attribution": out_a.attribution or None,
        },
        "b": {
            "url": _arena_asset_url(out_b),
            "format": out_b.asset_format,
            "output_id": out_b.id,
            "machine_generated": is_commercial_model(out_b.source),
            "attribution": out_b.attribution or None,
        },
    }


def _serialize_output(o: ModelOutput) -> dict:
    """Anonymized per-output payload for the 4-up K-wise ballot — the SAME fields `_serialize`
    exposes for a single output (url/format/output_id + the AUP machine-generated label). Never
    leaks generator identity. machine_generated/attribution carry the mandatory AI-provenance
    label so a commercial-model output shows the same badge in the K-wise grid as in the pair
    view — the labeling requirement is display-posture-wide, not pair-only."""
    from .public_export import is_commercial_model

    return {
        "output_id": o.id,
        "url": _arena_asset_url(o),
        "format": o.asset_format,
        "machine_generated": is_commercial_model(o.source),
        "attribution": o.attribution or None,
    }


def _build_gold_comparison(db: Session, session_id: str, crit: Criterion) -> dict | None:
    """Build a gold attention-check comparison (good vs decoy) with a known answer."""
    gp = matchmaking.pick_gold_pair(db)
    if gp is None:
        return None
    good = db.get(ModelOutput, gp.good_output_id)
    bad = db.get(ModelOutput, gp.bad_output_id)
    task = db.get(Task, gp.task_id)
    if good is None or bad is None or task is None:
        # Dangling gold pair: a referenced task/output was deleted (e.g. a data purge). The
        # create_all-only schema has no FK cascade, so guard rather than 500 the vote path.
        # Returning None makes the caller fall through to a real comparison.
        logger.warning("skipping dangling gold pair %s (task/output deleted)", gp.id)
        return None
    # Randomize which slot holds the good asset; gold_expected records it.
    if random.random() < 0.5:
        out_a, out_b, expected = good, bad, "a"
    else:
        out_a, out_b, expected = bad, good, "b"
    comparison = Comparison(
        task_id=task.id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=session_id,
        is_gold=True,
        gold_expected=expected,
    )
    db.add(comparison)
    db.commit()
    return _serialize(
        comparison, task, crit, out_a, out_b, service.reference_images_for_task(db, task)
    )


def _build_comparison(
    db: Session,
    session_id: str,
    criterion_slug: str | None = None,
    category_slug: str | None = None,
    kingdom: str = "all",
) -> dict | None:
    """Pick a task + pair (or inject a gold check), persist it, return anon payload."""
    crit = None
    if criterion_slug:
        crit = (
            db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
        )
    if crit is None:
        crit = _default_criterion(db)

    # Occasionally serve a gold attention check instead of a real comparison.
    if random.random() < config.GOLD_RATE:
        gold = _build_gold_comparison(db, session_id, crit)
        if gold is not None:
            return gold

    from .sourcing import is_reference_scan, is_untextured_output
    from . import admissibility

    category_id = _resolve_category_id(db, category_slug)

    # Precompute the gated output ids ONCE (per-output exclude_fn stays O(1)): the
    # admissibility composer unions structural ∪ completeness (∪ semantic when
    # SEMANTIC_ADMISSIBILITY_MODE=gate) behind one call.
    _gated = admissibility.non_admitted_output_ids(db)  # structural ∪ completeness ∪ semantic(gate)

    # Exclude from the perceptual vote pool: raw-scan reference outputs (render as ugly
    # point clouds, confound metric↔vote agreement) AND geometry-only outputs (flat grey
    # blobs that lose votes for lack of texture, not shape). Both stay in the Mode-B board.
    # Also exclude outputs auto-hidden (flag threshold) or D-Complete classified into a bad
    # completeness category (config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES).
    # Same predicate for task AND pair selection so pick_task never returns a task whose
    # only outputs pick_pair then excludes (which caused intermittent /api/next 404s).
    _app_hidden_gids = service.app_hidden_generator_ids(db)

    def _vote_excluded(o):
        return (
            is_reference_scan(o.source)
            or is_untextured_output(o)
            or o.hidden_at is not None
            or o.id in _gated
            or o.generator_id in _app_hidden_gids  # AgriGen internal testers: never in the pool
        )

    # Pairings this session already voted on: the /api/vote guard 409s a re-vote of any of
    # them, so exclude them from BOTH task and pair selection (same set for both, mirroring
    # the _vote_excluded parity) — else a session dead-ends re-served an already-voted pair.
    voted_pairs = integrity.voted_pairs_for(db, session_id, crit.id)

    # Kingdom scoping: an explicit ?category= is always within a kingdom, so both filters apply
    # together — pick_task's category_ids kwarg takes precedence over category_id, so intersect
    # them into one set here rather than pass both.
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    task = matchmaking.pick_task(
        db,
        category_ids=_effective_category_ids(k_ids, category_id),
        exclude_fn=_vote_excluded,
        voted_pairs=voted_pairs,
    )
    if task is None:
        return None
    pair = matchmaking.pick_pair(db, task, exclude_fn=_vote_excluded, voted_pairs=voted_pairs)
    if pair is None:
        return None
    out_a, out_b = pair
    comparison = Comparison(
        task_id=task.id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=session_id,
    )
    db.add(comparison)
    db.commit()
    return _serialize(
        comparison, task, crit, out_a, out_b, service.reference_images_for_task(db, task)
    )


def _build_kwise_comparison(
    db: Session,
    session_id: str,
    criterion_slug: str | None = None,
    category_slug: str | None = None,
    kingdom: str = "all",
) -> dict | None:
    """Serve a 4-up K-ballot (no gold in kwise). Falls back to a pairwise comparison when no task
    has >=4 admitted same-paradigm fresh outputs."""
    import json as _json
    import random as _random

    from .sourcing import is_reference_scan, is_untextured_output
    from . import admissibility

    crit = None
    if criterion_slug:
        crit = (
            db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
        )
    if crit is None:
        crit = _default_criterion(db)

    category_id = _resolve_category_id(db, category_slug)
    _gated = admissibility.non_admitted_output_ids(db)
    _app_hidden_gids = service.app_hidden_generator_ids(db)

    def _vote_excluded(o):
        return (
            is_reference_scan(o.source)
            or is_untextured_output(o)
            or o.hidden_at is not None
            or o.id in _gated
            or o.generator_id in _app_hidden_gids  # AgriGen internal testers: never in the pool
        )

    seen = integrity.seen_quads_for(db, session_id, crit.id)
    stmt = select(Task).where(Task.active.is_(True))
    if category_id is not None:
        stmt = stmt.where(Task.category_id == category_id)
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    tasks = list(db.execute(stmt).scalars().all())
    _random.shuffle(tasks)
    for task in tasks:
        quad = matchmaking.pick_quad(db, task, exclude_fn=_vote_excluded, seen_quads=seen)
        if quad is None:
            continue
        ballot = KBallot(
            task_id=task.id,
            criterion_id=crit.id,
            session_id=session_id,
            output_ids_json=_json.dumps([o.id for o in quad]),
        )
        db.add(ballot)
        db.commit()
        return {
            "kind": "kwise",
            "ballot_id": ballot.id,
            "task": {"id": task.id, "title": task.title, "prompt": task.prompt},
            "criterion": {"slug": crit.slug, "name": crit.name},
            "outputs": [_serialize_output(o) for o in quad],
        }
    # No quad anywhere → transparent pairwise fallback.
    return _build_comparison(db, session_id, criterion_slug, category_slug, kingdom=kingdom)


def _build_calibration_comparison(db: Session, session_id: str) -> dict | None:
    """Serve the next un-voted CalibrationPair for this session (with progress).

    Pairs whose task/criterion/outputs were deleted (dangling refs — the create_all schema
    has no FK cascade, so a data purge can leave them) are skipped: excluded from `total` AND
    never selected as target, so they can't 500 this path the way the gold path once did."""
    all_pairs = db.execute(select(CalibrationPair)).scalars().all()
    total = 0
    voted = 0
    target = None
    target_rows = None
    for cp in all_pairs:
        crit = db.get(Criterion, cp.criterion_id)
        task = db.get(Task, cp.task_id)
        out_a = db.get(ModelOutput, cp.output_a_id)
        out_b = db.get(ModelOutput, cp.output_b_id)
        if crit is None or task is None or out_a is None or out_b is None:
            continue  # dangling pair — unusable; exclude from progress + target selection
        total += 1
        already = integrity.already_voted_pair(
            db, session_id, cp.output_a_id, cp.output_b_id, cp.criterion_id
        )
        if already:
            voted += 1
        elif target is None:
            target = cp
            target_rows = (crit, task, out_a, out_b)
    progress = {"voted": voted, "total": total}
    if target_rows is None:
        return {"set": "calibration", "done": True, "progress": progress}

    crit, task, out_a, out_b = target_rows
    if random.random() < 0.5:
        out_a, out_b = out_b, out_a
    comparison = Comparison(
        task_id=task.id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=session_id,
    )
    db.add(comparison)
    db.commit()
    payload = _serialize(
        comparison, task, crit, out_a, out_b, service.reference_images_for_task(db, task)
    )
    payload["set"] = "calibration"
    payload["progress"] = progress
    return payload


def _require_admin(token: str | None) -> None:
    if not token or token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


def require_admin_header(x_admin_token: str | None = Header(default=None)) -> None:
    """Dependency for programmatic JSON/upload endpoints (token via X-Admin-Token)."""
    _require_admin(x_admin_token)


def require_admin_query(token: str | None = None) -> None:
    """Dependency for admin HTML pages (token via ?token= query). These GET pages render
    admin/moderation data (incl. submitter PII + un-vetted asset URLs), so they must not be
    world-readable even though the mutating POSTs are already token-gated."""
    _require_admin(token)


def require_internal_pages() -> None:
    """Dependency for the internal research/analytics pages. On the public instance
    (config.INTERNAL_PAGES_ENABLED is False) they hard-404, so novel methodology is
    unpublished — not merely admin-gated — on the public deploy. Read live so a deploy/test
    toggle of config.INTERNAL_PAGES_ENABLED takes effect without re-importing."""
    if not config.INTERNAL_PAGES_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


# ------------------------------------------------------------------- arena UI


def _hero_stats(db: Session) -> dict:
    """Cheap headline counts shared by the home hero and the arena hero, so both
    pages report the same numbers (votes cast / distinct models / active tasks /
    live kingdoms)."""

    return {
        "total_votes": matchmaking.total_votes(db),
        "models_count": db.execute(
            select(func.count(func.distinct(Generator.id))).where(Generator.kind == "model")
        ).scalar_one(),
        "tasks_count": db.execute(
            select(func.count(Task.id)).where(Task.active.is_(True))
        ).scalar_one(),
        "kingdoms_live": sum(1 for k in kingdoms.KINGDOMS if _kingdom_is_live(db, k)),
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    """Marketing/landing page. Never kingdom-gated (see `_roadmap_or_none` docstring) —
    it's the one screen every visitor should be able to load regardless of scope."""

    stats = _hero_stats(db)
    total_votes = stats["total_votes"]
    models_count = stats["models_count"]
    tasks_count = stats["tasks_count"]
    kingdoms_live = stats["kingdoms_live"]

    # "Choose a kingdom" cards below the hero — live/task-count are real per-kingdom queries
    # (not the top-level `kingdoms_live`/`tasks_count`, which are the all-kingdoms totals).
    kingdom_blurbs = {
        "plants": "Flowers, crops, and foliage — the founding kingdom of the benchmark.",
        "fungi": "Mushrooms and fruiting bodies — complement-aware completeness beyond plants.",
        "animals": "Vertebrates and invertebrates — arriving as tasks are seeded.",
    }
    kingdom_cards = []
    for k in kingdoms.KINGDOMS:
        k_ids = kingdoms.category_ids_for_kingdom(db, k)
        k_task_count = (
            db.execute(
                select(func.count(Task.id)).where(
                    Task.category_id.in_(k_ids), Task.active.is_(True)
                )
            ).scalar_one()
            if k_ids
            else 0
        )
        kingdom_cards.append(
            {
                "slug": k,
                "emoji": kingdoms.KINGDOM_EMOJI[k],
                "name": kingdoms.KINGDOM_LABEL[k],
                "latin": kingdoms.KINGDOM_LATIN.get(k, ""),
                "live": _kingdom_is_live(db, k),
                "blurb": kingdom_blurbs[k],
                "task_count": k_task_count,
            }
        )

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "total_votes": total_votes,
            "models_count": models_count,
            "tasks_count": tasks_count,
            "kingdoms_live": kingdoms_live,
            "kingdom_cards": kingdom_cards,
        },
    )


# Crawler-facing files. Both 404'd until the 2026-07-27 pre-release audit — a public launch
# with nothing telling a crawler what to index or skip.
#
# The allowlist below is deliberately explicit rather than derived from app.routes: the router
# also carries internal research pages, admin surfaces, JSON APIs and parameterised media
# routes, and a sitemap built by filtering all of those would advertise a new internal page the
# day someone adds one. Listing the public product surface by hand means a new page is absent
# until someone says otherwise, which is the safe direction to fail.
_SITEMAP_PATHS = (
    "/",
    "/arena",
    "/leaderboard",
    "/models",
    "/dataset",
    # No /spotlight: it is an internal page (see spotlight_index) and 404s on the public
    # instance, so advertising it here would hand crawlers a dead URL.
    "/methodology",
    "/coverage",
    "/tasks",
    "/submit",
    "/terms",
    "/privacy",
    "/licenses",
)


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    """Allow the product surface, keep crawlers off write/admin/API paths.

    Disallowing /api/ is not secrecy — those endpoints are already public where they should
    be. It stops a crawler burning the vote endpoints' rate limit and indexing JSON that has
    no standalone meaning.
    """
    lines = [
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /api/",
        "Disallow: /media/",
        "Allow: /",
        "",
        f"Sitemap: {config.PUBLIC_BASE_URL}/sitemap.xml",
        "",
    ]
    return "\n".join(lines)


@app.get("/sitemap.xml")
def sitemap_xml():
    urls = "".join(f"<url><loc>{config.PUBLIC_BASE_URL}{p}</loc></url>" for p in _SITEMAP_PATHS)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/arena", response_class=HTMLResponse)
def arena_page(request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    return templates.TemplateResponse(request, "arena.html", _hero_stats(db))


@app.get("/api/meta")
def api_meta(request: Request, db: Session = Depends(get_db)):
    """Categories + criteria for populating arena/leaderboard selectors."""
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    # Scope the category selector to the active kingdom so it never offers a category the
    # arena pool wouldn't actually serve (kingdom=all -> k_ids is None -> no filtering).
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    if k_ids is not None:
        cats = [c for c in cats if c.id in k_ids]
    # `coming_soon`: a category with no tasks is a roadmap placeholder (e.g. Fungi/Animals/
    # Microbes) — it self-activates the moment its first task is added. No schema flag.
    return {
        "categories": [{"slug": c.slug, "name": c.name, "coming_soon": not c.tasks} for c in cats],
        "criteria": [{"slug": c.slug, "name": c.name} for c in crits],
    }


@app.get("/api/next")
def api_next(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    mode: str | None = Query(default=None, alias="set"),
):
    if mode == "calibration":
        payload = _build_calibration_comparison(db, request.state.session_id)
    elif mode == "kwise":
        payload = _build_kwise_comparison(
            db, request.state.session_id, criterion, category, kingdom=request.state.kingdom
        )
    else:
        payload = _build_comparison(
            db, request.state.session_id, criterion, category, kingdom=request.state.kingdom
        )
    if payload is None:
        return JSONResponse({"error": "no-comparisons-available"}, status_code=404)
    return payload


@app.post("/api/vote")
def api_vote(
    vote_in: VoteIn,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    x_captcha_token: str | None = Header(default=None),
):
    sid = request.state.session_id

    # 1. Human verification (no-op unless REQUIRE_CAPTCHA is enabled).
    if not integrity.captcha_ok_for_session(sid, x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")
    # 2. Rate limiting — per session AND per IP (the IP layer caps cookie-reset farming).
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if not integrity.check_ip_rate_limit(request.state.client_ip):
        raise HTTPException(429, "Rate limit exceeded — slow down")

    comparison = db.get(Comparison, vote_in.comparison_id)
    if comparison is None:
        raise HTTPException(404, "Unknown comparison")
    if comparison.vote is not None:
        raise HTTPException(409, "Comparison already voted")
    # 3. Dedup: a session may not re-vote the same (non-gold) pairing.
    if not comparison.is_gold and integrity.already_voted_pair(
        db, sid, comparison.output_a_id, comparison.output_b_id, comparison.criterion_id
    ):
        raise HTTPException(409, "You have already voted on this pairing")

    vote = Vote(comparison_id=comparison.id, winner=vote_in.winner, session_id=sid)
    db.add(vote)
    db.flush()
    integrity.note_vote(db, sid)

    if comparison.is_gold:
        # Attention check: update trust, do NOT feed rankings.
        passed = vote_in.winner == comparison.gold_expected
        integrity.record_gold_outcome(db, sid, passed)
    else:
        service.apply_vote(db, vote)
    db.commit()
    # Keep the same criterion/category filter (+ active kingdom) for the follow-up comparison.
    nxt = _build_comparison(db, sid, criterion, category, kingdom=request.state.kingdom)

    # Post-vote reveal (Feature C): real generator names for the just-voted pair, ONLY for
    # non-gold comparisons — gold is an attention-check decoy, so revealing it would leak the
    # answer. Purely additive: never affects vote recording, dedup, or `next` above.
    reveal = None
    if not comparison.is_gold:
        out_a = db.get(ModelOutput, comparison.output_a_id)
        out_b = db.get(ModelOutput, comparison.output_b_id)
        names = service.generator_display_names(db)

        # Defensive: an output deleted between comparison-build and vote would be None here;
        # never 500 the (already-committed) vote's reveal — mirror the kvote guard below.
        def _rev_side(o: ModelOutput | None) -> dict:
            return {"name": names.get(o.generator_id, "Unknown") if o else "Unknown"}

        reveal = {"a": _rev_side(out_a), "b": _rev_side(out_b), "winner": vote_in.winner}
    return {"status": "ok", "next": nxt, "reveal": reveal}


@app.post("/api/kvote")
def api_kvote(
    kvote_in: KVoteIn,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    x_captcha_token: str | None = Header(default=None),
):
    import json as _json

    sid = request.state.session_id
    if not integrity.captcha_ok_for_session(sid, x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if not integrity.check_ip_rate_limit(request.state.client_ip):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    ballot = db.get(KBallot, kvote_in.ballot_id)
    if ballot is None:
        raise HTTPException(404, "Unknown ballot")
    if ballot.resolved:
        raise HTTPException(409, "Ballot already resolved")
    ids = _json.loads(ballot.output_ids_json)
    if kvote_in.best_output_id is not None and kvote_in.best_output_id not in ids:
        raise HTTPException(400, "best_output_id not among the shown outputs")
    service.resolve_kballot(db, ballot, kvote_in.best_output_id, sid)
    integrity.note_vote(db, sid)  # ONE rate-accounting per ballot, not per derived vote
    db.commit()
    nxt = _build_kwise_comparison(db, sid, criterion, category, kingdom=request.state.kingdom)

    # Post-vote reveal (Feature C): real generator names for every output shown in the ballot +
    # which one was picked, so the grid can label each card. K-wise never serves gold (see
    # _build_kwise_comparison docstring), so no omission case is needed here.
    names = service.generator_display_names(db)
    reveal_outputs = []
    for oid in ids:
        out = db.get(ModelOutput, oid)
        if out is None:
            continue  # defensive: dangling id, shouldn't happen but never 500 the reveal
        reveal_outputs.append({"output_id": oid, "name": names.get(out.generator_id, "Unknown")})
    reveal = {"outputs": reveal_outputs, "best_output_id": kvote_in.best_output_id}
    return {"status": "ok", "next": nxt, "reveal": reveal}


@app.post("/api/flag", dependencies=[Depends(require_internal_pages)])
def api_flag(flag_in: FlagIn, request: Request, db: Session = Depends(get_db)):
    """Report an output as not the organism / failed. CURATOR-ONLY: gated to the internal instance
    (the public deploy 404s here and renders no flag button), so it hides at the first flag
    (FLAG_HIDE_THRESHOLD default 1). Rate-limited; one flag per session per output; never advances."""
    from . import flags

    sid = request.state.session_id
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    if db.get(ModelOutput, flag_in.output_id) is None:
        raise HTTPException(404, "Unknown output")
    hidden, count = flags.record_flag(
        db, flag_in.output_id, sid, flag_in.reason, config.FLAG_HIDE_THRESHOLD
    )
    db.commit()
    return {"status": "ok", "hidden": hidden, "flags": count}


@app.get("/media/o/{output_id}.{ext}")
def media_asset(output_id: int, ext: str, db: Session = Depends(get_db)):
    """Resolve an opaque, output-scoped asset URL (emitted by _arena_asset_url) back to the real
    file, so the anonymized arena never exposes the descriptive asset_path. Serves by output id;
    `ext` is cosmetic (helps 3D viewers). Streams through the app on remote (S3) storage so the
    object key — which can encode identity — is never revealed to the client either."""
    o = db.get(ModelOutput, output_id)
    if o is None:
        raise HTTPException(404, "Unknown output")
    ctype = content_type_for(o.asset_path)
    if getattr(storage, "remote", False):
        return Response(content=storage.read(o.asset_path), media_type=ctype)
    path = config.ASSET_DIR / o.asset_path
    if not path.is_file():
        raise HTTPException(404, "Asset missing")
    return FileResponse(path, media_type=ctype)


# ------------------------------------------------------------------ leaderboard

# Provenance chips (paper/code/data) are a HEURISTIC, not real url fields — Generator /
# ModelOutput carry no paper/code/dataset link columns. The design brief's literal
# kind-based rule (model/agent/scan/baseline) doesn't match this repo's actual
# Generator.kind values (a full-repo grep finds only "model"/"decoy" ever set); `paradigm`
# is the axis that actually varies per generator, so the heuristic keys off paradigm
# instead, with the same intent: an approximate visual cue, never a claim of a real link.
_PARADIGM_PROVENANCE: dict[str, list[str]] = {
    "capture_scan": ["data"],
    "retrieval": ["data"],
    "agentic": ["code"],
    "procedural_llm": ["code"],
    "procedural_expert": ["paper", "code"],
    "image_recon": ["paper", "code"],
    "text_native": ["paper", "code"],
}


def _provenance_chips(paradigm: str | None, kind: str) -> list[str]:
    if kind == "baseline":
        return []
    return _PARADIGM_PROVENANCE.get(paradigm or "", ["code"])


def _avatar_initials(display_name: str) -> str:
    """2 uppercase initials from a generator's display name, e.g. 'Radim/gaussian (full)'
    -> 'RG', 'google/gemini-2.5-pro (agentic)' -> 'GG', single-word -> first 2 chars."""
    base = display_name.split("(")[0].strip()
    parts = [p for p in re.split(r"[/\s_·-]+", base) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if parts and len(parts[0]) >= 2:
        return parts[0][:2].upper()
    if parts:
        return (parts[0] + "?").upper()
    return "??"


def _avatar_hue(key: str) -> int:
    """Stable 0-360 hue derived from a generator's slug, so the avatar tile color is
    deterministic across requests/processes. Python's builtin `hash()` is per-process
    randomized for strings (security feature), so a stable hash (md5) substitutes for the
    literal `hash(slug) % 360` the brief describes."""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 360


def _trend_polyline(values: list[float | None], width: int = 64, height: int = 20) -> str:
    """SVG polyline `points` for the trend sparkline. Win-rate values are already a 0..1
    fraction. Empty/all-None -> a flat baseline (never a fabricated shape)."""
    pad = 2.0
    usable = [v for v in values if v is not None]
    if not usable:
        mid = height / 2.0
        return f"0,{mid:.1f} {width},{mid:.1f}"
    n = len(values)
    step = width / max(n - 1, 1)
    last_y = height / 2.0
    pts = []
    for i, v in enumerate(values):
        x = round(i * step, 1)
        if v is not None:
            last_y = pad + (1.0 - v) * (height - 2 * pad)
        pts.append(f"{x},{round(last_y, 1)}")
    return " ".join(pts)


def _trend_title(values: list[float | None]) -> str:
    """A readable hover title for the trend sparkline: the actual win-rate per period so the line
    conveys magnitude, not just shape. Win-rate values are a 0..1 fraction; None periods (no votes
    yet) are skipped rather than drawn as a fabricated 0%."""
    pct = [f"{v * 100:.0f}%" for v in values if v is not None]
    if not pct:
        return "No vote history yet"
    return "Win-rate by period: " + " → ".join(pct)


def _momentum(values: list[float | None]) -> str:
    """'up'/'down'/'flat' derived from the trend series — NOT a rank-vs-last-period delta
    (no historical rank snapshot table exists)."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return "flat"
    delta = vals[-1] - vals[0]
    if delta > 0.03:
        return "up"
    if delta < -0.03:
        return "down"
    return "flat"


def _enrich_leaderboard_rows(
    rows: list[dict], trend_by_gid: dict[int, list[float | None]]
) -> list[dict]:
    """Attach the prototype's display-only fields (avatar, provenance chips, trend
    sparkline + momentum, model-detail link, provisional flag, podium medal) to already-ranked
    leaderboard rows. Never touches bt_score/rank/ci_* — pure presentation enrichment."""
    # A medal is a claim of SEPARATION, so only a top-3 rank that no other displayed row shares —
    # and that real votes back — earns one. rank_by_ci lets CI-overlapping models share a rank; on
    # a thin board that can tie every model at rank 1, and five gold medals would read as five
    # winners. Those rows still show their (shared) rank number, which is the honest signal.
    rank_counts: dict[int, int] = {}
    for r in rows:
        rank_counts[r.get("rank", 0)] = rank_counts.get(r.get("rank", 0), 0) + 1
    for r in rows:
        rank = r.get("rank", 0)
        r["podium"] = r.get("n_games", 0) > 0 and rank <= 3 and rank_counts.get(rank, 0) == 1
    for r in rows:
        r["avatar"] = _avatar_initials(r["generator"])
        r["avatar_hue"] = _avatar_hue(r.get("slug") or r["generator"])
        r["provenance"] = _provenance_chips(r.get("paradigm"), r.get("kind", "model"))
        gid = r.get("generator_id")
        trend = trend_by_gid.get(gid, []) if gid is not None else []
        r["trend"] = trend
        r["trend_points"] = _trend_polyline(trend)
        r["trend_title"] = _trend_title(trend)
        r["momentum"] = _momentum(trend)
        r["provisional"] = r.get("n_games", 0) < service.FIRM_VOTE_THRESHOLD
        # Votes-until-firm signal for the board's Status column ({"firm": bool, "label": str}),
        # computed HERE so the template stays free of ranking/threshold logic.
        r["status"] = service.firm_status(r.get("n_games", 0))
        r["detail_url"] = f"/models/{r['slug']}" if r.get("slug") else "#"
    return rows


def _leaderboard_rows(
    db: Session,
    criterion_slug: str = "overall",
    category_slug: str | None = None,
    paradigm: str | None = None,
    kingdom: str = "all",
) -> list[dict]:
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    category_id = _resolve_category_id(db, category_slug)
    ref_gens = service.mode_a_excluded_generator_ids(db)
    names = service.generator_display_names(db)
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    if k_ids is not None:
        # A kingdom (≠ "all") is active: the cached `Rating` table is keyed by a single
        # category_id and cannot represent a SET of categories. `KingdomRating` (keyed by the
        # kingdom STRING) covers the whole-kingdom scope and is refreshed by /admin/recompute;
        # read it first and only fall back to live BT (kingdom_leaderboard_rows) on a cache miss
        # (nothing cached yet) — the page must be correct before the first recompute, just slow
        # that once. A narrower `?category=` selector WITHIN the kingdom has no cached
        # counterpart (the cache is whole-kingdom only), so it always computes live.
        ids = _effective_category_ids(k_ids, category_id)
        rows = None
        if category_id is None:
            rows = service.cached_kingdom_leaderboard_rows(db, criterion_slug, kingdom)
        if rows is None:
            rows = service.kingdom_leaderboard_rows(db, criterion_slug, ids)
        rows = [r for r in rows if not paradigm or r["paradigm"] == paradigm]
    else:
        scope = (
            Rating.category_id.is_(None)
            if category_id is None
            else Rating.category_id == category_id
        )
        ratings = (
            db.execute(select(Rating).where(Rating.criterion_id == crit.id, scope)).scalars().all()
        )
        rows = []
        for r in ratings:
            if r.generator_id in ref_gens:
                continue  # GT/reference scans don't compete in the Mode-A perceptual board
            gen = db.get(Generator, r.generator_id)
            if gen is None:
                continue  # stale rating row (generator deleted); skip rather than crash
            if paradigm and gen.paradigm != paradigm:
                continue
            rows.append(
                {
                    "generator": names.get(r.generator_id, gen.name),
                    "kind": gen.kind,
                    "paradigm": gen.paradigm,
                    "generator_id": r.generator_id,
                    "slug": gen.slug,
                    "elo": round(r.elo, 1),
                    "bt_score": round(r.bt_score, 1),
                    "bt_lower": round(r.bt_lower, 1),
                    "bt_upper": round(r.bt_upper, 1),
                    "n_games": r.n_games,
                }
            )
    # ONE flat table for the selected tab (design-parity task-lb): a `paradigm` filter
    # already narrowed `rows` to a single paradigm above, so "rank 1..N by bt_score desc"
    # is unambiguous there. With NO filter ("Overall") this DELIBERATELY merges rows from
    # every paradigm into one BT-desc ranking for the prototype's unified board — BT scores
    # across paradigms come from disconnected match components (spec §D's "never rank
    # across paradigms" invariant), so this is a display-only ordering, not a statistical
    # claim that e.g. rank 3 beats rank 4 across paradigms. The template/legend must footnote
    # that within-paradigm comparison (i.e. selecting a single paradigm tab) is the rigorous
    # one. finalize_rows() supplies the shared rank + whisker geometry (also used by the
    # verified board); CI-tie grouping still applies, just over the whole merged set.
    return service.finalize_rows(rows)


def _group_rank_judge_rows(rows: list[dict]) -> list[dict]:
    """Rank + CI-bar geometry computed WITHIN each paradigm group, mirroring
    _leaderboard_rows — cross-paradigm BT scores come from disconnected match
    components, so a single flat cross-paradigm ranking would be meaningless (I3b).
    Shared by the cached (global) and live (kingdom) judge-board paths."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["paradigm"], []).append(r)
    grouped_rows: list[dict] = []
    for pgm in sorted(groups):
        grows = groups[pgm]
        grows.sort(key=lambda x: x["bt_score"], reverse=True)
        ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in grows])
        for row, rank in zip(grows, ranks):
            row["rank"] = rank
        lo = min(r["bt_lower"] for r in grows)
        hi = max(r["bt_upper"] for r in grows)
        span = (hi - lo) or 1.0
        for r in grows:
            r["ci_left"] = round(100.0 * (r["bt_lower"] - lo) / span, 1)
            r["ci_width"] = round(100.0 * (r["bt_upper"] - r["bt_lower"]) / span, 1)
            r["ci_point"] = round(100.0 * (r["bt_score"] - lo) / span, 1)
            r["ci_lo"] = round(lo, 1)  # domain endpoints for the axis label (mirrors finalize_rows)
            r["ci_hi"] = round(hi, 1)
        grouped_rows.extend(grows)
    return grouped_rows


def _judge_leaderboard_rows(
    db: Session, criterion_slug: str = "overall", view_condition: str = "multi4"
) -> list[dict]:
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    ratings = (
        db.execute(
            select(JudgeRating).where(
                JudgeRating.criterion_id == crit.id,
                JudgeRating.view_condition == view_condition,
                JudgeRating.category_id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    ref_gens = service.mode_a_excluded_generator_ids(db)
    names = service.generator_display_names(db)
    rows = []
    for r in ratings:
        if r.generator_id in ref_gens:
            continue  # GT/reference scans don't compete in the Mode-A perceptual board
        gen = db.get(Generator, r.generator_id)
        if gen is None:
            continue  # stale rating row (generator deleted); skip rather than crash
        rows.append(
            {
                "generator": names.get(r.generator_id, gen.name),
                "kind": gen.kind,
                "paradigm": gen.paradigm,
                "elo": round(r.elo, 1),
                "bt_score": round(r.bt_score, 1),
                "bt_lower": round(r.bt_lower, 1),
                "bt_upper": round(r.bt_upper, 1),
                "n_games": r.n_games,
            }
        )
    return _group_rank_judge_rows(rows)


def _kingdom_judge_leaderboard_rows(
    db: Session,
    criterion_slug: str,
    view_condition: str,
    kingdom: str,
    category_ids: set[int] | None,
    category_id: int | None = None,
) -> list[dict]:
    """VLM-judge board for an active kingdom — mirrors _leaderboard_rows' kingdom branch: read
    the `KingdomJudgeRating` cache (refreshed by /admin/recompute) first, falling back to live BT
    (service.kingdom_judge_leaderboard_rows) on a cache miss or when a narrower `?category=`
    selector within the kingdom is active (the cache is whole-kingdom only, same convention as
    the human board)."""
    rows = None
    if category_id is None:
        rows = service.cached_kingdom_judge_leaderboard_rows(
            db, criterion_slug, view_condition, kingdom
        )
    if rows is None:
        rows = service.kingdom_judge_leaderboard_rows(
            db, criterion_slug, view_condition, category_ids
        )
    return _group_rank_judge_rows(rows)


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    paradigm: str | None = None,
    verified: bool = False,
    show_all: bool = False,
):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    paradigm = paradigm or None  # "" (unset <select>) and None both mean "no filter"
    kingdom = request.state.kingdom
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    category_id_sel = _resolve_category_id(db, category)
    cat_ids = _effective_category_ids(k_ids, category_id_sel) if k_ids is not None else None
    # `all_rows` is the UNIVERSE for this scope (every paradigm). It is NEVER displayed as one
    # merged board — the cross-paradigm ranking is gone (paradigms are disconnected match pools,
    # so a merged BT ordering was never a statistical claim). It feeds the trend series, the
    # rated/unrated counts, the tab list, and the per-modality grouping below.
    #
    # `verified` is a SCOPE MODIFIER (which votes count), not a board: it swaps the row source
    # (BT refit over signed-in votes only) and nothing else. Both scopes render the same two
    # surfaces — the modality hub (no paradigm) or ONE paradigm's board.
    if verified:
        all_rows = service.verified_leaderboard_rows(db, criterion, category, category_ids=cat_ids)
    else:
        all_rows = _leaderboard_rows(db, criterion, category, None, kingdom)

    # Paradigm -> that paradigm's rows. SHALLOW COPIES on purpose: service.finalize_rows() sorts
    # its list and rewrites `rank`/`ci_*` IN PLACE, so grouping the live all_rows dicts would let
    # a board's re-rank leak back into the universe that the counts/tabs/trend read from.
    groups: dict[str | None, list[dict]] = {}
    for r in all_rows:
        groups.setdefault(r.get("paradigm"), []).append(dict(r))

    total = matchmaking.total_votes(db)
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    # Real Vote.created-derived trend sparkline, scoped exactly like the rows above (kingdom
    # ids take precedence over a plain category id, mirroring _matches_for_scope elsewhere).
    crit_row = db.execute(select(Criterion).where(Criterion.slug == criterion)).scalars().first()
    trend_by_gid: dict[int, list[float | None]] = {}
    if all_rows and crit_row is not None:
        trend_by_gid = service.generator_trend_series(
            db,
            crit_row.id,
            None if cat_ids is not None else category_id_sel,
            category_ids=cat_ids,
        )

    def _finish(board_rows: list[dict]) -> list[dict]:
        """Select → RANK → enrich ONE board's rows. Rated-only by default (`show_all` reveals the
        never-voted entrants; fall back to all if none are rated).

        The rated filter runs BEFORE service.finalize_rows(), so `rank` is a CI-grouped 1..N over
        exactly the rows that will be DISPLAYED. Ranking first and filtering after (the old order)
        left the displayed rank a slice of a wider ranking — a board could start at rank 2 — which
        is why the template fell back to printing `loop.index` under the "Rank" header, inventing a
        strict order among CI-tied rows (on a zero-vote board: six models, identical BT, identical
        CIs, printed 1..6). Now the template prints the real CI-grouped rank: it starts at 1 and
        models that are not statistically separable SHARE a rank.

        `show_all` only reaches the USER on a single-paradigm board — the hub does not call this
        (service.modality_hub_cards owns its own rated/unrated split, since a card shows both a
        rated top-3 and an unvoted modality's empty state)."""
        rated = [r for r in board_rows if r.get("n_games", 0) > 0]
        shown = board_rows if (show_all or not rated) else rated
        enriched = _enrich_leaderboard_rows(service.finalize_rows(shown), trend_by_gid)
        # Fold re-hosts of one model (TRELLIS on fal / Replicate / local) under their canonical
        # row: three hosts of one model can otherwise take three separate rank slots and read as
        # three competitors. nest_variants re-ranks the top level so they consume none, and keeps
        # each variant's own BT + votes (see app/variants.py — scores are NOT merged).
        return variants.nest_variants(enriched)

    # Paradigms present in this (criterion/category/kingdom) scope — drives the tabs on a single
    # board. Derived from the merged all_rows so a tab never vanishes on click.
    paradigms_in_rows = sorted({r["paradigm"] for r in all_rows if r.get("paradigm")})

    # Share affordance + kingdom-scoped OG card for this board (rides on both the hub and a single
    # board, so it is built before the branch). See _leaderboard_share_context / render_leaderboard_card.
    lb_scope_label = (
        config.SITE_NAME
        if kingdom == "all"
        else kingdoms.KINGDOM_LABEL.get(kingdom, config.SITE_NAME)
    )
    lb_share = _leaderboard_share_context(lb_scope_label, kingdom)

    # Page-level chrome (_leaderboard_controls.html) — the category/criterion filters + the
    # Trusted/Verified scope toggle + the bias audit ride on BOTH the hub and a single board, so
    # they are built before the hub branch returns. `selected` flags are precomputed in Python so
    # the template avoids `==` (which the HTML formatter mangles inside Jinja tags).
    category_options = [
        {
            "slug": "all",
            "name": "All categories",
            "selected": category == "all",
            "coming_soon": False,
        }
    ]
    category_options += [
        {"slug": c.slug, "name": c.name, "selected": category == c.slug, "coming_soon": not c.tasks}
        for c in cats
    ]
    criterion_options = [
        {"slug": c.slug, "name": c.name, "selected": criterion == c.slug} for c in crits
    ]
    controls_ctx = {
        "category_options": category_options,
        "criterion_options": criterion_options,
        "bias": service.compute_bias(db),
        "sel_criterion": criterion,
        "sel_category": category,
        "verified": verified,
    }

    # NO paradigm filter (in EITHER scope) = the modality HUB: one card per visible modality,
    # linking to that modality's own board. There is no cross-paradigm ranking any more — BT
    # scores from different paradigms come from disconnected match pools. Each card re-ranks its
    # own group (a fresh within-paradigm 1..N — the only rigorous comparison); finalize_rows()
    # ranks whatever rows it is handed, so grouping the already-computed universe is identical to
    # issuing one row-query per paradigm, at a single query/BT read.
    #
    # The card set is the ROSTER's modalities (_visible_modalities), not "the modalities that have
    # votes in this scope": an unvoted modality still has a board (and /api/leaderboard still
    # publishes one), so it gets a card in an honest empty state rather than vanishing. rows_fn
    # hands modality_hub_cards the WHOLE group (rated + unrated) — it owns the split.
    if paradigm is None:
        cards = service.modality_hub_cards(lambda p: groups.get(p, []), _visible_modalities(db))
        return templates.TemplateResponse(
            request,
            "leaderboard_hub.html",
            {
                "cards": cards,
                "total_votes": total,
                "lb_share": lb_share,
                "firm_vote_threshold": service.FIRM_VOTE_THRESHOLD,
                **controls_ctx,
            },
        )

    # ONE paradigm's board, in either scope. Its rank is a FRESH within-paradigm 1..N (never a
    # slice of a merged ranking, which would read 3/7/9): finalize_rows() re-ranks this
    # paradigm's rows alone.
    rows = _finish(groups.get(paradigm, []))
    board_title = paradigms.DISPLAY_NAMES.get(paradigm, paradigm)
    # Global rated/unrated counts (for the single Show-all toggle) from the merged universe.
    total_generators = len(all_rows)
    unrated_count = sum(1 for r in all_rows if r.get("n_games", 0) == 0)
    # Tabs reflect the FULL paradigm universe for this scope (from all_rows, so a tab never
    # vanishes on click): "All methods" (back to the modality hub) first, then one tab per
    # paradigm. There is no cross-paradigm "Overall" tab — that ranking no longer exists.
    # `mode` tells the template which href/params to build.
    paradigm_options = [{"mode": "hub", "value": None, "tab": "All methods", "selected": False}] + [
        {
            "mode": "paradigm",
            "value": p,
            "display": paradigms.DISPLAY_NAMES.get(p, p),
            "tab": paradigms.SHORT_NAMES.get(p, p),
            "selected": paradigm == p,
        }
        for p in paradigms_in_rows
    ]
    return templates.TemplateResponse(
        request,
        "leaderboard.html",
        {
            "rows": rows,
            "board_title": board_title,
            # Plain-language "what this measures" line for THIS modality (never hard-coded copy —
            # paradigms.WHAT_THIS_MEASURES is the one source, shared with the hub cards).
            "board_what": paradigms.WHAT_THIS_MEASURES.get(paradigm, ""),
            "sel_paradigm": paradigm,
            "total_votes": total,
            "lb_share": lb_share,
            "paradigm_options": paradigm_options,
            "paradigm_display_names": paradigms.DISPLAY_NAMES,
            "show_all": show_all,
            "unrated_count": unrated_count,
            "total_generators": total_generators,
            "firm_vote_threshold": service.FIRM_VOTE_THRESHOLD,
            # The judge board is NOT computed here — it is fitted lazily by GET /leaderboard/judge
            # when the collapsed <details> is expanded (leaderboard.js), so the main render never
            # blocks on the ~11s judge BT fit for a kingdom on a cold cache.
            **controls_ctx,
        },
    )


def _visible_modalities(db: Session) -> list[str]:
    """The modalities that exist as a public surface — every paradigm carried by ≥1 generator in
    the roster, minus config.APP_HIDDEN_PARADIGMS, in paradigms.PARADIGMS order. This is the same
    universe the modality hub cards cover, so the judge page's switcher mirrors the human boards
    (rather than the judge board's own row set, which would drop a modality the judge hasn't
    scored yet and make the two surfaces disagree about which boards exist)."""
    present = set(db.execute(select(Generator.paradigm).distinct()).scalars())
    return [
        p
        for p in paradigms.PARADIGMS
        if p in present and p not in config.APP_HIDDEN_PARADIGMS and p is not None
    ]


@app.get("/leaderboard/judge", response_class=HTMLResponse)
def leaderboard_judge(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    modality: str | None = None,
    verified: bool = False,
    fragment: bool = False,
):
    """The VLM-judge board — a SEPARATE surface from the human-vote board, never intermixed with
    it (its BT scores come from VLM ballots, not human votes; the page says so in plain language).

    ONE route, TWO consumers:

    * A BROWSER arriving from a board's "see the AI-judge board →" link gets a full page
      (leaderboard_judge.html: site chrome, disclaimer, modality switcher, human-board backlink).
    * `app/static/leaderboard.js` lazy-fetches the SAME route into the collapsed <details> on the
      leaderboard and assigns the response to `innerHTML`, so it needs the BARE FRAGMENT
      (_leaderboard_judge.html) — a full page there would nest <html> inside a <div>. It is
      selected explicitly by `?fragment=1` (what the rendered `data-judge-url` carries) or by the
      `X-Requested-With` header leaderboard.js already sends, so a hand-typed URL — which has
      neither — always lands on the page.

    Rows are the same cache-first, live-fallback path the main route used to run inline (see
    _kingdom_judge_leaderboard_rows); the fit is ~11s cold, which is why the leaderboard never
    computes it on the main render. `verified` is accepted for URL symmetry with the human board;
    the judge board is unaffected by it (judge ballots have no signed-in scope)."""
    if modality is not None and (
        modality in config.APP_HIDDEN_PARADIGMS or not paradigms.is_valid_paradigm(modality)
    ):
        # Same contract as /leaderboard/{modality}: an app-hidden modality is internal-only and
        # must not exist as a public surface at all, judge board included.
        raise HTTPException(status_code=404, detail="Unknown modality")
    kingdom = request.state.kingdom
    k_ids = kingdoms.category_ids_for_kingdom(db, kingdom)
    category_id_sel = _resolve_category_id(db, category)
    cat_ids = _effective_category_ids(k_ids, category_id_sel) if k_ids is not None else None
    judge_rows = (
        _kingdom_judge_leaderboard_rows(db, criterion, "multi4", kingdom, cat_ids, category_id_sel)
        if k_ids is not None
        else _judge_leaderboard_rows(db, criterion, "multi4")
    )
    # Belt-and-braces with the generator-level hiding in service.app_hidden_generator_ids(): a
    # hidden paradigm never gets a heading, even if a stale rating row slipped through.
    judge_rows = [r for r in judge_rows if r.get("paradigm") not in config.APP_HIDDEN_PARADIGMS]
    if modality is not None:
        judge_rows = [r for r in judge_rows if r.get("paradigm") == modality]
    ctx = {"judge_rows": judge_rows, "paradigm_display_names": paradigms.DISPLAY_NAMES}
    if fragment or request.headers.get("x-requested-with"):
        return templates.TemplateResponse(request, "_leaderboard_judge.html", ctx)
    visible = _visible_modalities(db)
    return templates.TemplateResponse(
        request,
        "leaderboard_judge.html",
        {
            **ctx,
            # Suppresses the fragment's own inline provenance line — the page carries a louder one.
            "on_page": True,
            "sel_modality": modality,
            "sel_criterion": criterion,
            "sel_category": category,
            "board_title": paradigms.DISPLAY_NAMES.get(modality, modality) if modality else None,
            "board_what": paradigms.WHAT_THIS_MEASURES.get(modality, "") if modality else "",
            "modality_options": [
                {
                    "value": p,
                    "tab": paradigms.SHORT_NAMES.get(p, p),
                    "display": paradigms.DISPLAY_NAMES.get(p, p),
                    "selected": modality == p,
                }
                for p in visible
            ],
        },
    )


@app.get("/leaderboard/{modality}", response_class=HTMLResponse)
def leaderboard_modality(
    modality: str,
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    verified: bool = False,
    show_all: bool = False,
):
    """One modality's human-vote board — the destination of every hub card, and the canonical
    URL for a board (the `?paradigm=` form still works and renders the identical page).

    DECLARED AFTER `/leaderboard/judge` ON PURPOSE: Starlette matches routes in declaration
    order, so a `{modality}` route placed above it would swallow `/leaderboard/judge` and 404
    the judge board on the paradigm validation below (locked in by
    tests/test_modality_board_route.py::test_judge_route_is_not_shadowed_by_the_modality_path).

    Unknown OR app-hidden paradigms 404 rather than rendering an empty board: an internal-only
    modality (config.APP_HIDDEN_PARADIGMS) must not exist as a public surface at all."""
    if modality in config.APP_HIDDEN_PARADIGMS or not paradigms.is_valid_paradigm(modality):
        raise HTTPException(status_code=404, detail="Unknown modality")
    # Delegate to the single-paradigm branch of the existing handler — one board renderer, so the
    # path and query forms can never drift apart.
    return leaderboard(
        request,
        db,
        criterion=criterion,
        category=category,
        paradigm=modality,
        verified=verified,
        show_all=show_all,
    )


API_LEADERBOARD_NOTE = (
    "Every board ranks exactly ONE modality (paradigm). `rank` is always WITHIN a paradigm: "
    "BT scores from different paradigms come from disconnected match pools (models never face "
    "another modality), so a merged cross-paradigm ordering is not a statistical claim and this "
    "API does not emit one. `rows` is the concatenation of `boards` in modality order; pass "
    "?paradigm=<modality> for a single board."
)


@app.get("/api/leaderboard")
def api_leaderboard(
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    paradigm: str | None = None,
    verified: bool = False,
):
    """The JSON twin of the HTML leaderboard, under the SAME invariant: no cross-paradigm rank.

    It used to hand back one merged BT ordering ranked 1..N over every paradigm at once (and to
    ignore `?paradigm=` entirely in the `verified` branch) — the last surface still publishing a
    ranking across disconnected match pools. Now it mirrors the pages:

    * `?paradigm=<modality>` → that ONE board, freshly ranked 1..N within itself (the same
      service.finalize_rows() call the HTML board runs on the paradigm-filtered subset — no BT
      refit, no change to the ranking math). Unknown / app-hidden modalities 404, as on the page.
    * no `?paradigm=` → `boards`: one per modality, each independently ranked. `rows` stays for
      back-compat (app/client.py's `leaderboard()` reads it) as the boards concatenated in
      modality order — so every `rank` it carries is a within-paradigm one, and there are as many
      rank-1 rows as there are modalities. There is no merged rank anywhere in the response.

    `verified` is a SCOPE modifier (which votes count), never a board of its own: it swaps the row
    source and is then grouped/ranked identically.
    """
    paradigm = paradigm or None  # "" (unset filter) and None both mean "no filter"
    if paradigm is not None and (
        paradigm in config.APP_HIDDEN_PARADIGMS or not paradigms.is_valid_paradigm(paradigm)
    ):
        raise HTTPException(status_code=404, detail="Unknown modality")

    if verified:
        all_rows = service.verified_leaderboard_rows(db, criterion, category)
    else:
        all_rows = _leaderboard_rows(db, criterion, category, None)
    # Belt-and-braces with the generator-level hiding already applied by both row sources
    # (service.app_hidden_generator_ids covers APP_HIDDEN_PARADIGMS): an internal-only modality is
    # not a public surface, JSON included.
    all_rows = [r for r in all_rows if r.get("paradigm") not in config.APP_HIDDEN_PARADIGMS]

    # Group first, rank second. COPIES: service.finalize_rows() sorts and rewrites rank/ci_* in
    # place, so a board's re-rank must not leak back into the merged rows it came from.
    groups: dict[str, list[dict]] = {}
    for r in all_rows:
        groups.setdefault(r.get("paradigm") or "", []).append(dict(r))
    if paradigm is not None:
        keys = [paradigm]
    else:
        # Registry order first, then any leftover value (notably "" — un-backfilled generators,
        # which form ONE match pool of their own per paradigms.same_paradigm).
        keys = [p for p in paradigms.PARADIGMS if p in groups]
        keys += sorted(k for k in groups if k not in paradigms.PARADIGMS)
    boards = [
        {
            "paradigm": k or None,
            "display_name": paradigms.DISPLAY_NAMES.get(k) or "Unclassified",
            # Fresh within-paradigm 1..N (never a slice of a merged ranking).
            "rows": service.finalize_rows(groups.get(k, [])),
        }
        for k in keys
    ]
    return {
        "criterion": criterion,
        "category": category,
        "paradigm": paradigm,
        "verified": verified,
        "note": API_LEADERBOARD_NOTE,
        "boards": boards,
        "rows": [r for b in boards for r in b["rows"]],
    }


def _model_cards(db: Session, k_ids: set[int] | None) -> list[dict]:
    """Per-generator directory rows for /models: coverage stats + a WITHIN-METHOD BT score/rank,
    matched by the same unique display name `coverage_summary`/`_leaderboard_rows` compute
    internally (both derive it from `service.generator_display_names`, so matching on it is safe).
    No fabricated org/company field — `Generator` has none (name/kind/paradigm/description only).

    The `rank` is computed PER PARADIGM (the same group-then-finalize_rows pattern the /leaderboard
    route runs), over that modality's RATED entrants — never over the merged universe. The grid this
    feeds is sectioned by modality (_model_sections) for the same reason the leaderboard is: BT
    scores from different paradigms come from disconnected match pools, so one BT-descending order
    over every generator at once is not a comparison. /models used to BE that order — sorting the
    whole public grid by the merged BT number and printing it uncaveated on every card — which is
    the last cross-paradigm ranking on the site; the merged number itself is unchanged (a
    generator's BT does not depend on how rows are grouped), only what it is sorted by and how it
    is labelled.
    """
    names = service.generator_display_names(db)
    cov_by_name = {
        r["generator"]: r for r in service.coverage_summary(db, category_ids=k_ids)["generators"]
    }
    # One read of the BT universe; only the per-generator NUMBERS (bt_score/n_games) are taken from
    # it. Its own merged rank/order is deliberately discarded — see the rank pass below.
    universe = _leaderboard_rows(db, "overall", "all", None)
    bt_by_name = {r["generator"]: r for r in universe}
    by_paradigm: dict[str | None, list[dict]] = {}
    for r in universe:
        by_paradigm.setdefault(r.get("paradigm") or None, []).append(dict(r))  # copies: see below
    # Within-method rank: finalize_rows() (the boards' own ranker — no BT refit, no change to the
    # ranking math) over each modality's rated rows alone, so it starts at 1 and CI-tied models
    # share a number. Unrated entrants get no rank — they carry only the default prior.
    rank_by_name: dict[str, int] = {}
    for rows in by_paradigm.values():
        for r in service.finalize_rows([r for r in rows if r.get("n_games", 0) > 0]):
            rank_by_name[r["generator"]] = r["rank"]
    app_hidden = service.app_hidden_generator_ids(db)

    cards = []
    for g in db.execute(select(Generator)).scalars().all():
        if g.id in app_hidden:
            continue  # AgriGen internal testers: hidden from the app UI (kept in DB for analysis)
        disp_name = names.get(g.id, g.name)
        cov = cov_by_name.get(disp_name)
        if cov is None:
            continue  # gold-only / empty generators don't appear (mirrors coverage_summary)
        bt = bt_by_name.get(disp_name)
        cards.append(
            {
                "slug": g.slug,
                "name": disp_name,
                "kind": g.kind,
                "avatar": _avatar_initials(disp_name),
                "avatar_hue": _avatar_hue(g.slug or disp_name),
                "paradigm": g.paradigm,
                "paradigm_display": paradigms.DISPLAY_NAMES.get(g.paradigm, g.paradigm)
                if g.paradigm
                else "",
                "description": g.description,
                "bt_score": bt["bt_score"] if bt else None,
                "rank": rank_by_name.get(disp_name),
                "votes": cov["votes"],
                "tasks": cov["tasks"],
                "confidence": cov["confidence"],
            }
        )
    # Sorted WITHIN a modality (BT desc, unscored last) — the flat list is only ever consumed
    # per-section (_model_sections) or by slug (model_detail); it is not a ranking of its own.
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    cards.sort(
        key=lambda c: (
            c["paradigm"] is None,
            order.get(c["paradigm"], len(order)),
            c["bt_score"] is None,
            -(c["bt_score"] or 0),
            c["name"],
        )
    )
    return cards


def _model_sections(cards: list[dict], show_all: bool) -> list[dict]:
    """Group the model directory BY MODALITY, in `paradigms.PARADIGMS` order — the same spine the
    leaderboard hub uses, so /models and /leaderboard agree about what a BT score means (a rank
    within ONE method). Each section shows its rated models by default; `show_all` reveals the
    never-voted entrants, and a section whose models are all unrated keeps an honest empty state
    rather than disappearing. Generators with no paradigm land in a trailing "Unclassified"
    section (they are one match pool of their own — see paradigms.same_paradigm)."""
    order = {p: i for i, p in enumerate(paradigms.PARADIGMS)}
    by_p: dict[str | None, list[dict]] = {}
    for c in cards:
        by_p.setdefault(c["paradigm"] or None, []).append(c)
    sections = []
    for p in sorted(by_p, key=lambda x: (x is None, order.get(x, len(order)))):
        group = by_p[p]
        rated = [c for c in group if c.get("votes", 0) > 0]
        sections.append(
            {
                "paradigm": p,
                "display": paradigms.DISPLAY_NAMES.get(p, p) if p else "Unclassified",
                "what": paradigms.WHAT_THIS_MEASURES.get(p, "") if p else "",
                "cards": group if show_all else rated,
                "model_count": len(group),
                "rated_count": len(rated),
                # An app-hidden paradigm has no public board (its generators are already filtered
                # out of `cards`, so this is belt-and-braces), and neither has "Unclassified".
                "board_url": f"/leaderboard/{p}"
                if p and p not in config.APP_HIDDEN_PARADIGMS
                else None,
            }
        )
    return sections


@app.get("/models", response_class=HTMLResponse)
def models_index(request: Request, db: Session = Depends(get_db), show_all: bool = False):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    cards = _model_cards(db, k_ids)
    # Rated-only by default: generators never voted on (votes == 0) carry only the default prior
    # BT and flood the grid — hide them behind a "Show all" toggle. Fall back to all if none rated.
    total_generators = len(cards)
    rated_cards = [c for c in cards if c.get("votes", 0) > 0]
    unrated_count = total_generators - len(rated_cards)
    sections = _model_sections(cards, show_all or not rated_cards)
    return templates.TemplateResponse(
        request,
        "models.html",
        {
            "sections": sections,
            "shown_count": sum(len(s["cards"]) for s in sections),
            "show_all": show_all,
            "unrated_count": unrated_count,
            "total_generators": total_generators,
        },
    )


# --------------------------------------------------------------- shareable result cards (#75)
# A shared model link must unfurl into a card showing that model's CURRENT standing — ranks move
# as votes land, so the image is a live route, not a baked asset. The bytes are cached in-process
# against the data they were drawn from, so a burst of unfurls redraws once.
_OG_CARD_CACHE: dict[str, tuple[str, bytes]] = {}


def _og_cache_key(db: Session, gen: Generator) -> str:
    """Everything the card can change with, in two cheap aggregates: the ratings' `updated` stamp
    (a recompute rewrites every row, so a rank shift anywhere invalidates every card) and this
    generator's own vote tally (which moves per-vote, before the next recompute)."""
    ratings_v = db.execute(select(func.max(Rating.updated))).scalar()
    votes_v = db.execute(
        select(func.sum(ModelOutput.n_comparisons)).where(ModelOutput.generator_id == gen.id)
    ).scalar()
    return f"{gen.id}:{ratings_v}:{votes_v}"


def _model_share_context(db: Session, gen: Generator, cards: list[dict] | None = None) -> dict:
    """The facts behind a model's share card + og:description.

    GLOBAL scope on purpose (`_model_cards(db, None)`): a shared link must unfurl the same way for
    everyone, not according to whichever kingdom the sharer happened to have selected.

    The rank is a WITHIN-METHOD rank and is labelled as one everywhere it appears — every board on
    this site ranks exactly one paradigm (disconnected match pools), so a bare site-wide "#2"
    would be a claim the ranking math does not back. `rank_of` counts the models actually RANKED
    in this method (finalize_rows' rated set — an unrated entrant carries only the default prior
    and is not a rung on the ladder).
    """
    if cards is None:
        cards = _model_cards(db, None)
    card = next((c for c in cards if c["slug"] == gen.slug), None)
    modality = paradigms.DISPLAY_NAMES.get(gen.paradigm, gen.paradigm) or "an unclassified method"
    name = card["name"] if card else gen.name
    votes = int(card["votes"]) if card else 0
    rank = card["rank"] if card else None
    bt_score = card["bt_score"] if card else None
    rank_of = (
        sum(1 for c in cards if c["paradigm"] == gen.paradigm and c["rank"] is not None)
        if rank
        else 0
    )
    status = service.firm_status(votes)
    standing = og.model_standing(
        modality=modality,
        rank=rank,
        rank_of=rank_of,
        bt_score=bt_score,
        votes=votes,
        firm=status["firm"],
        firm_label=status["label"],
    )
    description = og.share_description(
        name=name,
        modality=modality,
        standing=standing,
        bt_score=bt_score,
        votes=votes,
        site_name=config.SITE_NAME,
    )
    page_url = _abs_url(f"/models/{gen.slug}")
    tweet = f"{name} — {standing['headline']} on {config.SITE_NAME}."
    return {
        "name": name,
        "modality": modality,
        "bt_score": bt_score,
        "rank": rank,
        "rank_of": rank_of,
        "votes": votes,
        "firm": status["firm"],
        "firm_label": status["label"],
        "standing": standing,
        "description": description,
        "page_url": page_url,
        "og_image_url": _abs_url(f"/og/models/{gen.slug}.png"),
        "x_intent_url": (f"https://x.com/intent/post?text={quote(tweet)}&url={quote(page_url)}"),
    }


@app.get("/og/models/{slug}.png")
def model_og_card(slug: str, db: Session = Depends(get_db)):
    """The per-model Open Graph card, drawn from CURRENT data (app.og). 404s for an app-hidden
    generator exactly like /models/{slug} does — an unfurl must never leak an internal model."""
    gen = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
    if gen is None or gen.id in service.app_hidden_generator_ids(db):
        raise HTTPException(404, "Unknown generator")
    key = _og_cache_key(db, gen)
    cached = _OG_CARD_CACHE.get(slug)
    if cached is not None and cached[0] == key:
        png = cached[1]
    else:
        ctx = _model_share_context(db, gen)
        png = og.render_model_card(
            name=ctx["name"],
            modality=ctx["modality"],
            bt_score=ctx["bt_score"],
            rank=ctx["rank"],
            rank_of=ctx["rank_of"],
            votes=ctx["votes"],
            firm=ctx["firm"],
            firm_label=ctx["firm_label"],
        )
        _OG_CARD_CACHE[slug] = (key, png)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


def _leaderboard_card_facts(db: Session, kingdom: str) -> tuple[str, int, int, int]:
    """(scope_label, n_models, n_methods, votes) for a kingdom's board card. Scope label is the
    site name for the all-kingdoms view (a card headline of "All kingdoms leaderboard" reads worse
    than "Bio 3D Arena leaderboard"); a specific kingdom keeps its own label. Vote count is the
    site-wide total, matching the leaderboard page header's own framing."""
    rows = _leaderboard_rows(db, "overall", "all", None, kingdom)
    n_methods = len({r.get("paradigm") for r in rows if r.get("paradigm")})
    scope_label = (
        config.SITE_NAME
        if kingdom == "all"
        else kingdoms.KINGDOM_LABEL.get(kingdom, config.SITE_NAME)
    )
    return scope_label, len(rows), n_methods, matchmaking.total_votes(db)


def _leaderboard_share_context(scope_label: str, kingdom: str) -> dict:
    """Share affordance for a leaderboard view: the canonical page URL (humans keep their own
    kingdom), the kingdom-scoped OG card (so the unfurl preview shows the sharer's board), and an
    X-intent. Mirrors _model_share_context so leaderboard.js reuses the same share.js handlers."""
    page_url = _abs_url("/leaderboard")
    tweet = (
        f"{scope_label} — Bradley–Terry 3D-generation rankings from blind human votes "
        f"on {config.SITE_NAME}."
    )
    return {
        "scope_label": scope_label,
        "page_url": page_url,
        "og_image_url": _abs_url(f"/og/leaderboard.png?scope={quote(kingdom)}"),
        "x_intent_url": f"https://x.com/intent/post?text={quote(tweet)}&url={quote(page_url)}",
    }


# Cached like the per-model card: the BT refit inside _leaderboard_card_facts is not free, and an
# unfurl bot may hit this repeatedly. Keyed on (kingdom, total_votes) so it self-invalidates the
# moment a vote lands.
_LB_OG_CACHE: dict[str, tuple[int, bytes]] = {}


@app.get("/og/leaderboard.png")
def leaderboard_og_card(scope: str = "all", db: Session = Depends(get_db)):
    """The leaderboard Open Graph card, kingdom-scoped, drawn from current data (app.og).

    The kingdom is passed as `scope`, NOT `kingdom`, on purpose: the http middleware reads a
    `?kingdom=` query param on every request and persists it to a cookie, so naming it `kingdom`
    here would let an unfurl of one kingdom's card flip the viewer's own board scope."""
    kingdom = kingdoms.normalize_kingdom(scope)
    total = matchmaking.total_votes(db)
    cached = _LB_OG_CACHE.get(kingdom)
    if cached is not None and cached[0] == total:
        png = cached[1]
    else:
        scope_label, n_models, n_methods, votes = _leaderboard_card_facts(db, kingdom)
        png = og.render_leaderboard_card(
            scope_label=scope_label, n_models=n_models, n_methods=n_methods, votes=votes
        )
        _LB_OG_CACHE[kingdom] = (total, png)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=600"},
    )


@app.get("/models/{slug}", response_class=HTMLResponse)
def model_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    gen = db.execute(select(Generator).where(Generator.slug == slug)).scalars().first()
    if gen is None or gen.id in service.app_hidden_generator_ids(db):
        raise HTTPException(404, "Unknown generator")  # app-hidden testers: not reachable by URL
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    cards = _model_cards(db, k_ids)
    card = next((c for c in cards if c["slug"] == slug), None)
    # The share card / meta description are kingdom-INDEPENDENT (see _model_share_context); reuse
    # the cards we already have only when the current scope IS the global one.
    share = _model_share_context(db, gen, cards=cards if k_ids is None else None)

    outs = [o for o in gen.outputs if not o.is_gold and o.hidden_at is None]
    by_task: dict[int, dict] = {}
    for o in outs:
        t = o.task
        if t is None:
            continue
        row = by_task.setdefault(
            t.id,
            {
                "task": t.title,
                "category": t.category.name if t.category else "",
                "outputs": 0,
                "votes": 0,
            },
        )
        row["outputs"] += 1
        row["votes"] += o.n_comparisons
    task_rows = sorted(by_task.values(), key=lambda r: (-r["outputs"], r["task"]))

    samples = []
    for o in outs[:6]:
        samples.append(
            {
                "id": o.id,
                "title": o.title or (o.task.title if o.task else ""),
                "asset_url": storage.url_for(o.asset_path),
                "format": o.asset_format,
            }
        )

    # Head-to-head record (#74): the evidence behind the rank. Opponents are same-paradigm by
    # construction (see service.head_to_head_record) — the template must label it as a
    # within-method record, and ties are shown, not folded away.
    names = service.generator_display_names(db)
    h2h = [
        {**rec, "opponent_name": names.get(rec["opponent_id"], "Unknown")}
        for rec in service.head_to_head_record(db, gen.id, "overall", category_ids=k_ids)
    ]

    return templates.TemplateResponse(
        request,
        "model_detail.html",
        {
            "gen": gen,
            "card": card,
            "task_rows": task_rows,
            "samples": samples,
            "h2h": h2h,
            "share": share,
        },
    )


@app.get("/dataset", response_class=HTMLResponse)
def dataset_page(request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    releases_dir = config.RELEASES_DIR
    releases = []
    if releases_dir.is_dir():
        for d in sorted(releases_dir.iterdir(), reverse=True):
            vf = d / "VERSION"
            if d.is_dir() and vf.is_file():
                releases.append({"version": d.name, "version_text": vf.read_text()})
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    composition = dataset.dataset_composition(db, k_ids)
    return templates.TemplateResponse(
        request, "dataset.html", {"releases": releases, "composition": composition}
    )


@app.get("/methodology", response_class=HTMLResponse)
def methodology_page(request: Request):
    return templates.TemplateResponse(
        request,
        "methodology.html",
        {
            "rate_limit": config.VOTE_RATE_LIMIT,
            "rate_window": int(config.VOTE_RATE_WINDOW),
            "gold_rate": config.GOLD_RATE,
            "trust_threshold": config.TRUST_THRESHOLD,
            "require_captcha": config.REQUIRE_CAPTCHA,
        },
    )


@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html")


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/licenses", response_class=HTMLResponse)
def licenses_page(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(ModelOutput.license, ModelOutput.attribution, ModelOutput.source).distinct()
    ).all()
    return templates.TemplateResponse(request, "licenses.html", {"licenses": rows})


@app.get("/coverage", response_class=HTMLResponse)
def coverage_page(request: Request, db: Session = Depends(get_db)):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    summary = service.coverage_summary(db, category_ids=k_ids)
    return templates.TemplateResponse(
        request,
        "coverage.html",
        {
            "generators": summary["generators"],
            "tasks": summary["tasks"],
            "by_paradigm": summary.get("by_paradigm", {}),
            "paradigm_display_names": paradigms.DISPLAY_NAMES,
            "firm_threshold": service.FIRM_VOTE_THRESHOLD,
            "trait_board": service.trait_leaderboard(db),
            "mode_c_experimental": not service.accepted_trait_classes(db),
        },
    )


@app.get("/api/coverage.json")
def api_coverage(db: Session = Depends(get_db)):
    return service.coverage_summary(db)


@app.get(
    "/research",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def research_hub(request: Request):
    """Single sidebar entry point for the analysis boards, which used to occupy three
    separate top-level slots (/benchmark, /significance, /difficulty) while /fidelity and
    /procedural had no nav entry at all. Static links only — each board loads its own data,
    so the hub adds no queries."""
    return templates.TemplateResponse(request, "research.html", {})


@app.get(
    "/procedural",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def procedural_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "procedural.html",
        {"rows": service.procedural_scorecard(db)},
    )


@app.get("/api/procedural.json", dependencies=[Depends(require_internal_pages)])
def api_procedural(db: Session = Depends(get_db)):
    return service.procedural_scorecard(db)


@app.get("/api/completeness.json")
def api_completeness(db: Session = Depends(get_db)):
    return service.completeness_rows(db)


@app.get("/api/dgen.json")
def api_dgen(db: Session = Depends(get_db)):
    return service.dgen_trajectory(db)


@app.get("/api/fidelity.json", dependencies=[Depends(require_internal_pages)])
def api_fidelity(db: Session = Depends(get_db)):
    return fidelity.fidelity_scorecard(db)


@app.get(
    "/fidelity",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def fidelity_board(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "fidelity.html", {"board": fidelity.fidelity_scorecard(db)}
    )


# --------------------------------------------------------- significance + bias


@app.get("/api/significance", dependencies=[Depends(require_internal_pages)])
def api_significance(
    db: Session = Depends(get_db), criterion: str = "overall", category: str = "all"
):
    return service.compute_significance(db, criterion, _resolve_category_id(db, category))


@app.get("/api/bias", dependencies=[Depends(require_internal_pages)])
def api_bias(db: Session = Depends(get_db)):
    return service.compute_bias(db)


@app.get(
    "/significance",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def significance_page(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
    show_all: bool = False,
):
    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    category_id = _resolve_category_id(db, category)
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    # Rated-only by default: a never-voted generator has no significance signal and floods the
    # forest plot + P(A>B) matrix. `?show_all=true` includes them.
    if k_ids is not None:
        sig = service.compute_significance(
            db,
            criterion,
            category_ids=_effective_category_ids(k_ids, category_id),
            rated_only=not show_all,
        )
    else:
        sig = service.compute_significance(db, criterion, category_id, rated_only=not show_all)
    # Forest-plot CI bounds: REUSE the leaderboard's cached BT confidence interval per
    # generator (same kingdom scoping the leaderboard route branches on) rather than
    # computing new stats — bt_lower/bt_upper are absolute values so merging rows across
    # paradigm groups here is safe even though _leaderboard_rows computes rank/percent
    # geometry per-paradigm internally. A generator sig.ranked knows about but that has no
    # leaderboard row (scope mismatch) simply has no entry; the template renders it as a
    # bare point rather than fabricating an interval.
    lb_rows = _leaderboard_rows(db, criterion, category, None, request.state.kingdom)
    ci_map = {r["generator"]: (r["bt_lower"], r["bt_upper"]) for r in lb_rows}
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    category_options = [
        {
            "slug": "all",
            "name": "All categories",
            "selected": category == "all",
            "coming_soon": False,
        }
    ]
    category_options += [
        {"slug": c.slug, "name": c.name, "selected": category == c.slug, "coming_soon": not c.tasks}
        for c in cats
    ]
    criterion_options = [
        {"slug": c.slug, "name": c.name, "selected": criterion == c.slug} for c in crits
    ]
    return templates.TemplateResponse(
        request,
        "significance.html",
        {
            "sig": sig,
            "bias": service.compute_bias(db),
            "category_options": category_options,
            "criterion_options": criterion_options,
            "ci_map": ci_map,
            "show_all": show_all,
            "sel_criterion": criterion,
            "sel_category": category,
        },
    )


# ------------------------------------------------------------------------ tasks


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, db: Session = Depends(get_db)):

    from .models import ReconTask, TaskDifficulty

    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    stmt = select(Task).order_by(Task.id)
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    tasks = db.execute(stmt).scalars().all()
    task_ids = [t.id for t in tasks]

    # Real vote totals per task (non-gold decisive votes) — reused for both the per-row
    # VOTES column and the "votes across tasks" stat card, so the two numbers can never
    # silently disagree.
    vote_counts: dict[int, int] = (
        dict(
            db.execute(
                select(Comparison.task_id, func.count(Vote.id))
                .select_from(Vote)
                .join(Comparison, Vote.comparison_id == Comparison.id)
                .where(Comparison.is_gold.is_(False), Comparison.task_id.in_(task_ids))
                .group_by(Comparison.task_id)
            ).all()
        )
        if task_ids
        else {}
    )

    # Difficulty tier, keyed by task — same table/join the /difficulty page reads from.
    # Tasks without a curated row simply have no tier (shown as "—"), never a guess.
    tier_by_task: dict[int, str] = (
        dict(
            db.execute(
                select(TaskDifficulty.task_id, TaskDifficulty.tier).where(
                    TaskDifficulty.task_id.in_(task_ids)
                )
            ).all()
        )
        if task_ids
        else {}
    )

    # Latin binomial, keyed by task — same ReconTask.species_name the /difficulty page's
    # tier_species header line reads. Tasks with no recon-GT bundle fall back to the title.
    species_by_task: dict[int, str] = (
        dict(
            db.execute(
                select(ReconTask.task_id, ReconTask.species_name).where(
                    ReconTask.task_id.in_(task_ids)
                )
            ).all()
        )
        if task_ids
        else {}
    )

    # Distinct paradigms actually exercised by non-gold outputs in scope — the third stat
    # card. Omitted entirely (not zeroed) if nothing is tagged, so the card never fakes 0.
    paradigm_stmt = (
        (
            select(Generator.paradigm)
            .distinct()
            .join(ModelOutput, ModelOutput.generator_id == Generator.id)
            .where(ModelOutput.is_gold.is_(False), ModelOutput.task_id.in_(task_ids))
        )
        if task_ids
        else None
    )
    paradigms_exercised = (
        {p for p in db.execute(paradigm_stmt).scalars().all() if p}
        if paradigm_stmt is not None
        else set()
    )

    def _paradigm_label(t: Task) -> str:
        tagged = {o.generator.paradigm for o in t.outputs if not o.is_gold and o.generator.paradigm}
        if len(tagged) == 1:
            return paradigms.DISPLAY_NAMES.get(next(iter(tagged)), next(iter(tagged)))
        if len(tagged) > 1:
            return "Multiple"
        return "—"

    rows = []
    for t in tasks:
        cat = t.category
        kingdom = kingdoms.KINGDOM_OF.get(cat.slug, "all")
        species_name = species_by_task.get(t.id) or ""
        rows.append(
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt,
                "category": cat.name,
                "n_outputs": len(t.outputs),
                "active": t.active,
                "kingdom_emoji": kingdoms.KINGDOM_EMOJI.get(kingdom, kingdoms.KINGDOM_EMOJI["all"]),
                "species_name": species_name,
                "paradigm": _paradigm_label(t),
                "tier": tier_by_task.get(t.id),
                "votes": vote_counts.get(t.id, 0),
            }
        )
    stats = {
        "live_tasks": sum(1 for t in tasks if t.active),
        "votes_total": sum(vote_counts.values()) if vote_counts else None,
        "n_paradigms": len(paradigms_exercised) if paradigms_exercised else None,
    }
    return templates.TemplateResponse(request, "tasks.html", {"tasks": rows, "stats": stats})


@app.get(
    "/spotlight",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def spotlight_index(request: Request, db: Session = Depends(get_db)):
    from . import spotlight

    subjects = sorted(spotlight.SPOTLIGHTS, key=lambda s: (not s["featured"], s["order"]))
    kingdom = kingdoms.normalize_kingdom(request.state.kingdom)
    if kingdom != "all":
        subjects = [s for s in subjects if s.get("kingdom") == kingdom]
    counts = spotlight.model_counts(db)
    subjects = [{**s, "model_count": counts.get(s["slug"])} for s in subjects]
    return templates.TemplateResponse(request, "spotlight_index.html", {"subjects": subjects})


@app.get(
    "/spotlight/{slug}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def spotlight_page(slug: str, request: Request, db: Session = Depends(get_db)):
    from . import spotlight

    data = spotlight.build_spotlight(db, slug)
    if data is None:
        raise HTTPException(404, "spotlight not found")
    return templates.TemplateResponse(request, "spotlight.html", {"s": data})


# ------------------------------------------------------------------------ admin


@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin_query)])
def admin_page(request: Request, db: Session = Depends(get_db)):
    ctx = {
        "categories": db.execute(select(Category)).scalars().all(),
        "criteria": db.execute(select(Criterion)).scalars().all(),
        "generators": db.execute(select(Generator)).scalars().all(),
        "tasks": db.execute(select(Task)).scalars().all(),
    }
    return templates.TemplateResponse(request, "admin.html", ctx)


@app.post("/admin/category")
def admin_create_category(
    token: str = Form(...),
    slug: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Category(slug=slug, name=name, description=description))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/criterion")
def admin_create_criterion(
    token: str = Form(...),
    slug: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Criterion(slug=slug, name=name, description=description))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/generator")
def admin_create_generator(
    token: str = Form(...),
    slug: str = Form(...),
    name: str = Form(...),
    kind: str = Form("model"),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Generator(slug=slug, name=name, kind=kind, description=description))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/task")
def admin_create_task(
    token: str = Form(...),
    category_id: int = Form(...),
    title: str = Form(...),
    prompt: str = Form(...),
    criteria_note: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    db.add(Task(category_id=category_id, title=title, prompt=prompt, criteria_note=criteria_note))
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/output")
async def admin_upload_output(
    token: str = Form(...),
    task_id: int = Form(...),
    generator_id: int = Form(...),
    title: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    ext = (file.filename or "asset.glb").rsplit(".", 1)[-1].lower()
    rel = str(Path("uploads") / f"{uuid.uuid4().hex}.{ext}").replace("\\", "/")
    storage.save(rel, await file.read())
    db.add(
        ModelOutput(
            task_id=task_id,
            generator_id=generator_id,
            title=title,
            asset_path=rel,
            asset_format=ext,
            meta_json=json.dumps({"uploaded": True, "filename": file.filename}),
        )
    )
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/recompute")
def admin_recompute(token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    detail = service.recompute_all(db)
    return JSONResponse({"status": "recomputed", "detail": detail})


@app.post("/admin/recompute_judge")
def admin_recompute_judge(
    token: str = Form(...), view_condition: str = Form("multi4"), db: Session = Depends(get_db)
):
    _require_admin(token)
    detail = service.recompute_judge_all(db, view_condition=view_condition)
    return JSONResponse({"status": "recomputed", "detail": detail})


@app.post("/admin/rescore")
def admin_rescore(token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    from . import recon_service, structure_service

    detail = recon_service.rescore_all(db)
    # Second Mode-B axis: organ-structure fidelity for structure-known (procedural) outputs.
    organ_detail = structure_service.rescore_all(db)
    return JSONResponse({"status": "rescored", "detail": detail, "organ": organ_detail})


def _default_benchmark_task_id(db: Session, category_ids: set[int] | None = None) -> int | None:
    """Return the first task_id that has a ReconTask row AND at least one ok Metric.

    Falls back to tasks[0].id if no scored task exists yet. Returns None if no tasks.
    Used by benchmark_page to avoid defaulting to an unscored task (P1-3 fix).
    `category_ids` (when given) restricts candidates to the active kingdom, mirroring the
    picker list built by benchmark_page.
    """
    from .models import Metric, ReconTask, Task

    stmt = select(Task)
    if category_ids is not None:
        stmt = stmt.where(Task.category_id.in_(category_ids))
    tasks = db.execute(stmt).scalars().all()
    if not tasks:
        return None
    for t in tasks:
        rt = db.execute(select(ReconTask).where(ReconTask.task_id == t.id)).scalars().first()
        if rt is None:
            continue
        out_ids = [o.id for o in t.outputs if not o.is_gold]
        if not out_ids:
            continue
        has_score = (
            db.execute(select(Metric).where(Metric.output_id.in_(out_ids), Metric.status == "ok"))
            .scalars()
            .first()
        )
        if has_score:
            return t.id
    return tasks[0].id


@app.get(
    "/benchmark",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def benchmark_page(request: Request, db: Session = Depends(get_db), task_id: int | None = None):
    from . import recon_service
    from .models import Task

    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    stmt = select(Task)
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    tasks = db.execute(stmt).scalars().all()
    if task_id is None and tasks:
        task_id = _default_benchmark_task_id(db, category_ids=k_ids)
    board = recon_service.recon_method_leaderboard(db, task_id) if task_id else []
    confounds = recon_service.recon_confounds(db, task_id) if task_id else None
    agree = (
        recon_service.agreement(db, task_id)
        if task_id
        else {"status": "empty", "spearman": None, "rows": []}
    )
    viewer_outputs = recon_service.recon_outputs_for_task(db, task_id) if task_id else []
    reference = recon_service.reference_for_task(db, task_id) if task_id else None
    current = db.get(Task, task_id) if task_id else None
    vote_category = current.category.slug if current and current.category else None
    cross_species = recon_service.cross_species_summary(db)
    return templates.TemplateResponse(
        request,
        "benchmark.html",
        {
            "tasks": tasks,
            "task_id": task_id,
            "board": board,
            "confounds": confounds,
            "agree": agree,
            "viewer_outputs": viewer_outputs,
            "reference": reference,
            "vote_category": vote_category,
            "cross_species": cross_species,
        },
    )


@app.get("/api/benchmark", dependencies=[Depends(require_internal_pages)])
def api_benchmark(db: Session = Depends(get_db), task_id: int | None = None):
    from . import recon_service

    if task_id is None:
        return JSONResponse({"error": "task_id required"}, status_code=400)
    return JSONResponse(
        {
            "leaderboard": recon_service.recon_method_leaderboard(db, task_id),
            "per_output": recon_service.recon_leaderboard(db, task_id),
            "confounds": recon_service.recon_confounds(db, task_id),
            "agreement": recon_service.agreement(db, task_id),
        }
    )


# ------------------------------------------------------------------ data export


@app.get("/api/export.json")
def export_dataset(request: Request, db: Session = Depends(get_db)):
    """Reproducible research export: every decided comparison with full provenance.

    Generators are revealed here (post-hoc), enabling offline ranking studies. Scoped by the
    active kingdom (query param / cookie), matching every other data page (`all` == unfiltered).
    """
    from . import dataset as dataset_mod

    return dataset_mod.build_preference_records(db, kingdom=request.state.kingdom)


@app.get(
    "/difficulty",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def difficulty_page(request: Request, db: Session = Depends(get_db)):
    """Render the per-tier objective scorecard + the cross-tier degradation gradient."""
    from .models import ReconTask, Task, TaskDifficulty

    roadmap = _roadmap_or_none(request, db)
    if roadmap is not None:
        return roadmap
    k_ids = kingdoms.category_ids_for_kingdom(db, request.state.kingdom)
    scorecard = difficulty.tier_scorecard(db, category_ids=k_ids)
    tiers = list(difficulty.TIERS)

    # Species sitting in each tier (for the per-tier header line).
    tier_species: dict[str, list[str]] = {}
    stmt = (
        select(TaskDifficulty, Task, ReconTask)
        .join(Task, Task.id == TaskDifficulty.task_id)
        .join(ReconTask, ReconTask.task_id == TaskDifficulty.task_id, isouter=True)
    )
    if k_ids is not None:
        stmt = stmt.where(Task.category_id.in_(k_ids))
    rows = db.execute(stmt).all()
    for td, task, rt in rows:
        name = (rt.species_name if rt else None) or task.title
        tier_species.setdefault(td.tier, []).append(name)

    # Cross-tier gradient: generators scored in >=2 tiers, mean chamfer per tier — this is the
    # headline (does a method degrade easy→moderate→hard?).
    by_gen: dict[str, dict[str, float | None]] = {}
    for block in scorecard:
        if block["tier"] not in tiers:
            continue
        for r in block["rows"]:
            if r["mean_chamfer"] is not None:
                by_gen.setdefault(r["generator"], {})[block["tier"]] = r["mean_chamfer"]
    gradient = [
        {"generator": gen, "chamfer": {t: ch.get(t) for t in tiers}}
        for gen, ch in by_gen.items()
        if sum(1 for t in tiers if ch.get(t) is not None) >= 2
    ]
    gradient.sort(key=lambda g: g["generator"].lower())

    # perceptual/trait_tiers/reliability stay global (JudgeVote/TraitScore paths have no
    # category-scoping wired yet — see report for the explicit scoped-vs-global split).
    perceptual = service.tier_perceptual_ranking(db)
    trait_tiers = service.tier_trait_accuracy(db)
    paradigm_grid = difficulty.paradigm_tier_scorecard(db, category_ids=k_ids)
    # Reference/capture-quality triage: taxa where recon completeness is far below text (the
    # recon INPUT is suspect). Sorted by gap desc; flagged ones shown first.
    from .completeness import recon_reliability_flags

    reliability = recon_reliability_flags(db)

    return templates.TemplateResponse(
        request,
        "difficulty.html",
        {
            "scorecard": scorecard,
            "tier_species": tier_species,
            "tiers": tiers,
            "gradient": gradient,
            "perceptual": perceptual,
            "trait_tiers": trait_tiers,
            "paradigm_grid": paradigm_grid,
            "paradigm_display_names": paradigms.DISPLAY_NAMES,
            "reliability": reliability,
        },
    )


@app.get("/api/difficulty.json", dependencies=[Depends(require_internal_pages)])
def api_difficulty(db: Session = Depends(get_db)):
    """Per-tier objective scorecard (× generator and × paradigm) over existing metrics, plus the
    recon-reliability triage flags (taxa whose recon completeness is far below text→3D)."""
    from .completeness import recon_reliability_flags

    return {
        "scorecard": difficulty.tier_scorecard(db),
        "paradigm_grid": difficulty.paradigm_tier_scorecard(db),
        "recon_reliability": recon_reliability_flags(db),
    }


# ----------------------------------------------------------- Mode-C trait scoring


@app.get("/api/trait_scores.json", dependencies=[Depends(require_internal_pages)])
def api_trait_scores(db: Session = Depends(get_db)):
    """Mode-C botanical-accuracy: generator leaderboard + per-output scores."""
    from .models import TraitScore

    outputs = [
        {
            "output_id": ts.output_id,
            "botanical_accuracy": ts.botanical_accuracy,
            "n_scored": ts.n_scored,
            "n_total": ts.n_total,
        }
        for ts in db.execute(select(TraitScore)).scalars()
    ]
    return {"generators": service.trait_leaderboard(db), "outputs": outputs}


@app.get("/api/traits.json", dependencies=[Depends(require_internal_pages)])
def api_traits(db: Session = Depends(get_db)):
    """The literature-sourced trait rubrics (one per taxon/task)."""
    from .models import TraitRubric

    rubrics = []
    for r in db.execute(select(TraitRubric)).scalars():
        try:
            traits = json.loads(r.traits_json or "[]")
        except json.JSONDecodeError:
            traits = []
        rubrics.append({"taxon": r.taxon, "task_id": r.task_id, "traits": traits})
    return {"rubrics": rubrics}


@app.get(
    "/trait/{output_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_internal_pages)],
)
def trait_scorecard_page(output_id: int, request: Request, db: Session = Depends(get_db)):
    """Per-output Mode-C scorecard: each verdict joined to its rubric trait + the score."""
    from .models import TraitRubric, TraitScore, TraitVerdict

    output = db.get(ModelOutput, output_id)
    if output is None:
        raise HTTPException(404, "Unknown output")

    rubric = (
        db.execute(select(TraitRubric).where(TraitRubric.task_id == output.task_id))
        .scalars()
        .first()
    )
    rubric_by_key: dict[str, dict] = {}
    if rubric is not None:
        try:
            for t in json.loads(rubric.traits_json or "[]"):
                rubric_by_key[t.get("key")] = t
        except json.JSONDecodeError:
            pass

    accepted = service.accepted_trait_classes(db)
    verdicts = (
        db.execute(select(TraitVerdict).where(TraitVerdict.output_id == output_id)).scalars().all()
    )
    rows = []
    for v in verdicts:
        meta = rubric_by_key.get(v.trait_key, {})
        rows.append(
            {
                "trait_key": v.trait_key,
                "trait_class": v.trait_class,
                "expected": meta.get("expected", "—"),
                "citation": meta.get("citation", "—"),
                "verdict": v.verdict,
                "rationale": v.rationale,
                "calibrated": v.trait_class in accepted,
            }
        )
    rows.sort(key=lambda r: (not r["calibrated"], r["trait_class"], r["trait_key"]))

    score = (
        db.execute(select(TraitScore).where(TraitScore.output_id == output_id)).scalars().first()
    )
    task = db.get(Task, output.task_id)
    return templates.TemplateResponse(
        request,
        "trait_scorecard.html",
        {
            "output_id": output_id,
            "task": task,
            "taxon": rubric.taxon if rubric else None,
            "rows": rows,
            "score": score,
            "mode_c_experimental": not accepted,
        },
    )


# ----------------------------------------------------------- ingestion API (JSON)
# Programmatic surface for generator pipelines. Auth via the X-Admin-Token header.


@app.get("/api/tasks")
def api_list_tasks(db: Session = Depends(get_db)):
    """Discover task ids/slugs so a pipeline knows where to register outputs."""
    out = []
    for t in db.execute(select(Task)).scalars().all():
        out.append(
            {
                "id": t.id,
                "title": t.title,
                "category": t.category.slug,
                "n_outputs": len(t.outputs),
                "active": t.active,
            }
        )
    return {"tasks": out}


@app.post("/api/categories", dependencies=[Depends(require_admin_header)])
def api_upsert_category(body: CategoryIn, db: Session = Depends(get_db)):
    cat = ingest.upsert_category(db, body.slug, body.name, body.description)
    db.commit()
    return {"id": cat.id, "slug": cat.slug, "name": cat.name}


@app.post("/api/generators", dependencies=[Depends(require_admin_header)])
def api_upsert_generator(body: GeneratorIn, db: Session = Depends(get_db)):
    gen = ingest.upsert_generator(db, body.slug, body.name, body.kind, body.description)
    db.commit()
    return {"id": gen.id, "slug": gen.slug, "name": gen.name, "kind": gen.kind}


@app.post("/api/tasks", dependencies=[Depends(require_admin_header)])
def api_create_task(body: TaskIn, db: Session = Depends(get_db)):
    try:
        task = ingest.create_task(db, body.category, body.title, body.prompt, body.criteria_note)
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"id": task.id, "title": task.title, "category": task.category.slug}


@app.post("/api/outputs", dependencies=[Depends(require_admin_header)])
async def api_register_output(
    task_id: int = Form(...),
    generator_slug: str = Form(...),
    generator_name: str | None = Form(default=None),
    title: str = Form(default=""),
    meta: str = Form(default="{}"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Register a generator's baked 3D asset for a task (validated + deduped)."""
    ext = (file.filename or "asset.glb").rsplit(".", 1)[-1].lower()
    try:
        meta_dict = json.loads(meta) if meta else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"meta must be valid JSON: {exc}") from exc
    try:
        output, created = ingest.register_output(
            db,
            task_id=task_id,
            generator_slug=generator_slug,
            data=await file.read(),
            ext=ext,
            title=title,
            meta=meta_dict,
            generator_name=generator_name,
        )
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {
        "id": output.id,
        "created": created,  # False if an identical asset was already registered
        "task_id": output.task_id,
        "generator_id": output.generator_id,
        "asset_url": storage.url_for(output.asset_path),
        "format": output.asset_format,
        "meta": json.loads(output.meta_json),
    }


# --------------------------------------------------- community submission queue


@app.get("/submit", response_class=HTMLResponse)
def submit_page(request: Request, db: Session = Depends(get_db)):
    tasks = db.execute(select(Task).where(Task.active.is_(True))).scalars().all()
    return templates.TemplateResponse(
        request,
        "submit.html",
        {"tasks": [{"id": t.id, "title": t.title, "category": t.category.name} for t in tasks]},
    )


@app.post("/api/submit")
async def api_submit(
    request: Request,
    task_id: int = Form(...),
    generator_slug: str = Form(...),
    generator_name: str = Form(default=""),
    title: str = Form(default=""),
    submitter: str = Form(default=""),
    meta: str = Form(default="{}"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_captcha_token: str | None = Header(default=None),
):
    """Public: queue a community 3D output for moderation (rate-limited, validated)."""
    sid = request.state.session_id
    if not integrity.captcha_ok_for_session(sid, x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")
    if not integrity.check_rate_limit(sid):
        raise HTTPException(429, "Rate limit exceeded — slow down")
    ext = (file.filename or "asset.glb").rsplit(".", 1)[-1].lower()
    try:
        meta_dict = json.loads(meta) if meta else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"meta must be valid JSON: {exc}") from exc
    try:
        sub = submissions.create_submission(
            db,
            task_id=task_id,
            generator_slug=generator_slug,
            data=await file.read(),
            ext=ext,
            title=title,
            meta=meta_dict,
            submitter=submitter,
            session_id=sid,
            generator_name=generator_name,
        )
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"status": "pending", "submission_id": sub.id}


@app.get("/api/submissions", dependencies=[Depends(require_admin_header)])
def api_submissions(db: Session = Depends(get_db), status: str | None = None):
    subs = submissions.list_submissions(db, status)
    return {
        "submissions": [
            {
                "id": s.id,
                "task_id": s.task_id,
                "generator_slug": s.generator_slug,
                "title": s.title,
                "status": s.status,
                "submitter": s.submitter,
                "asset_url": storage.url_for(s.asset_path),
                "format": s.asset_format,
            }
            for s in subs
        ]
    }


@app.get(
    "/admin/moderation",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin_query)],
)
def moderation_page(request: Request, db: Session = Depends(get_db)):
    pending = submissions.list_submissions(db, status="pending")
    rows = []
    for s in pending:
        task = db.get(Task, s.task_id)
        rows.append(
            {
                "id": s.id,
                "task": task.title if task else f"#{s.task_id}",
                "generator_slug": s.generator_slug,
                "generator_name": s.generator_name,
                "title": s.title,
                "submitter": s.submitter,
                "asset_url": storage.url_for(s.asset_path),
                "format": s.asset_format,
            }
        )

    from . import flags as _flags

    flagged_outputs = (
        db.query(ModelOutput)
        .join(OutputFlag, OutputFlag.output_id == ModelOutput.id)
        .distinct()
        .all()
    )
    flagged = []
    for o in flagged_outputs:
        flagged.append(
            {
                "id": o.id,
                "asset_url": storage.url_for(o.asset_path),
                "task": o.task.title if o.task else f"#{o.task_id}",
                "flags": _flags.distinct_flag_count(db, o.id),
                "hidden": o.hidden_at is not None,
            }
        )
    return templates.TemplateResponse(
        request, "moderation.html", {"pending": rows, "flagged": flagged}
    )


@app.post("/admin/submissions/{submission_id}/approve")
def admin_approve(
    submission_id: int,
    token: str = Form(...),
    note: str = Form(default=""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    try:
        submissions.approve(db, submission_id, note)
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    # Carry the token so the redirect lands on the now token-gated moderation page.
    return RedirectResponse(f"/admin/moderation?token={quote(token)}", status_code=303)


@app.post("/admin/submissions/{submission_id}/reject")
def admin_reject(
    submission_id: int,
    token: str = Form(...),
    note: str = Form(default=""),
    db: Session = Depends(get_db),
):
    _require_admin(token)
    try:
        submissions.reject(db, submission_id, note)
    except ingest.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    # Carry the token so the redirect lands on the now token-gated moderation page.
    return RedirectResponse(f"/admin/moderation?token={quote(token)}", status_code=303)


@app.post("/admin/outputs/{output_id}/hide")
def admin_hide_output(output_id: int, token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    out = db.get(ModelOutput, output_id)
    if out is None:
        raise HTTPException(404, "Unknown output")
    if out.hidden_at is None:
        out.hidden_at = _models_utcnow()
    db.commit()
    return RedirectResponse(f"/admin/moderation?token={quote(token)}", status_code=303)


@app.post("/admin/outputs/{output_id}/restore")
def admin_restore_output(output_id: int, token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    out = db.get(ModelOutput, output_id)
    if out is None:
        raise HTTPException(404, "Unknown output")
    out.hidden_at = None
    db.commit()
    return RedirectResponse(f"/admin/moderation?token={quote(token)}", status_code=303)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": app.version}
