"""FastAPI application — arena, voting, leaderboard, tasks, and admin tools."""

from __future__ import annotations

import json
import logging
import random
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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, difficulty, ingest, integrity, matchmaking, ranking, service, submissions
from .database import get_db, init_db
from .models import (
    CalibrationPair,
    Category,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    ModelOutput,
    Rating,
    Task,
    Vote,
)
from .schemas import CategoryIn, GeneratorIn, TaskIn, VoteIn
from .storage import get_storage

logger = logging.getLogger(__name__)

config.ensure_dirs()
init_db()

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Bio 3D Arena", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
# Local backend serves assets from disk; the S3 backend serves them from the bucket/CDN.
storage = get_storage()
if not storage.remote:
    app.mount("/assets", StaticFiles(directory=str(config.ASSET_DIR)), name="assets")

SESSION_COOKIE = "bio3d_session"


@app.middleware("http")
async def ensure_session(request: Request, call_next):
    """Attach an anonymous session id (cookie) used for light dedup + history."""
    sid = request.cookies.get(SESSION_COOKIE)
    is_new = sid is None
    if is_new:
        sid = uuid.uuid4().hex
    request.state.session_id = sid
    response = await call_next(request)
    if is_new:
        response.set_cookie(
            SESSION_COOKIE, sid, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365
        )
    return response


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


def _serialize(
    comparison: Comparison, task: Task, crit: Criterion, out_a: ModelOutput, out_b: ModelOutput
) -> dict:
    """Anonymized arena payload — never leaks generator identity or gold status."""
    return {
        "comparison_id": comparison.id,
        "task": {"title": task.title, "prompt": task.prompt, "category": task.category.name},
        "criterion": {"slug": crit.slug, "name": crit.name},
        "a": {"url": storage.url_for(out_a.asset_path), "format": out_a.asset_format},
        "b": {"url": storage.url_for(out_b.asset_path), "format": out_b.asset_format},
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
    return _serialize(comparison, task, crit, out_a, out_b)


def _build_comparison(
    db: Session,
    session_id: str,
    criterion_slug: str | None = None,
    category_slug: str | None = None,
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

    category_id = _resolve_category_id(db, category_slug)

    # Exclude from the perceptual vote pool: raw-scan reference outputs (render as ugly
    # point clouds, confound metric↔vote agreement) AND geometry-only outputs (flat grey
    # blobs that lose votes for lack of texture, not shape). Both stay in the Mode-B board.
    # Same predicate for task AND pair selection so pick_task never returns a task whose
    # only outputs pick_pair then excludes (which caused intermittent /api/next 404s).
    def _vote_excluded(o):
        return is_reference_scan(o.source) or is_untextured_output(o)

    task = matchmaking.pick_task(db, category_id=category_id, exclude_fn=_vote_excluded)
    if task is None:
        return None
    pair = matchmaking.pick_pair(db, task, exclude_fn=_vote_excluded)
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
    return _serialize(comparison, task, crit, out_a, out_b)


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
    payload = _serialize(comparison, task, crit, out_a, out_b)
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


# ------------------------------------------------------------------- arena UI


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "arena.html")


@app.get("/api/meta")
def api_meta(db: Session = Depends(get_db)):
    """Categories + criteria for populating arena/leaderboard selectors."""
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
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
    else:
        payload = _build_comparison(db, request.state.session_id, criterion, category)
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
    if not integrity.verify_captcha(x_captcha_token):
        raise HTTPException(403, "Captcha verification required/failed")
    # 2. Rate limiting.
    if not integrity.check_rate_limit(sid):
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
    # Keep the same criterion/category filter for the follow-up comparison.
    nxt = _build_comparison(db, sid, criterion, category)
    return {"status": "ok", "next": nxt}


# ------------------------------------------------------------------ leaderboard


def _leaderboard_rows(
    db: Session, criterion_slug: str = "overall", category_slug: str | None = None
) -> list[dict]:
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    category_id = _resolve_category_id(db, category_slug)
    scope = (
        Rating.category_id.is_(None) if category_id is None else Rating.category_id == category_id
    )
    ratings = (
        db.execute(select(Rating).where(Rating.criterion_id == crit.id, scope)).scalars().all()
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
                "elo": round(r.elo, 1),
                "bt_score": round(r.bt_score, 1),
                "bt_lower": round(r.bt_lower, 1),
                "bt_upper": round(r.bt_upper, 1),
                "n_games": r.n_games,
            }
        )
    rows.sort(key=lambda x: x["bt_score"], reverse=True)
    # CI-grouped rank (overlapping 95% CIs share a rank), computed on the displayed
    # (rounded) bounds so the rank matches the numbers shown.
    ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    for row, rank in zip(rows, ranks):
        row["rank"] = rank
    # CI whisker-bar geometry: position each [lower, point, upper] as a percent of the
    # column's full value span so ties are visible at a glance.
    if rows:
        lo = min(r["bt_lower"] for r in rows)
        hi = max(r["bt_upper"] for r in rows)
        span = (hi - lo) or 1.0
        for r in rows:
            r["ci_left"] = round(100.0 * (r["bt_lower"] - lo) / span, 1)
            r["ci_width"] = round(100.0 * (r["bt_upper"] - r["bt_lower"]) / span, 1)
            r["ci_point"] = round(100.0 * (r["bt_score"] - lo) / span, 1)
    return rows


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
                "elo": round(r.elo, 1),
                "bt_score": round(r.bt_score, 1),
                "bt_lower": round(r.bt_lower, 1),
                "bt_upper": round(r.bt_upper, 1),
                "n_games": r.n_games,
            }
        )
    rows.sort(key=lambda x: x["bt_score"], reverse=True)
    ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    for row, rank in zip(rows, ranks):
        row["rank"] = rank
    return rows


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
):
    rows = _leaderboard_rows(db, criterion, category)
    total = matchmaking.total_votes(db)
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    # Precompute `selected` flags in Python so the template avoids `==` (which the
    # HTML formatter mangles inside Jinja tags).
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
        "leaderboard.html",
        {
            "rows": rows,
            "total_votes": total,
            "category_options": category_options,
            "criterion_options": criterion_options,
            "bias": service.compute_bias(db),
            "sel_criterion": criterion,
            "sel_category": category,
            "judge_rows": _judge_leaderboard_rows(db, criterion, "multi4"),
        },
    )


@app.get("/api/leaderboard")
def api_leaderboard(
    db: Session = Depends(get_db), criterion: str = "overall", category: str = "all"
):
    return {
        "criterion": criterion,
        "category": category,
        "rows": _leaderboard_rows(db, criterion, category),
    }


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


# --------------------------------------------------------- significance + bias


@app.get("/api/significance")
def api_significance(
    db: Session = Depends(get_db), criterion: str = "overall", category: str = "all"
):
    return service.compute_significance(db, criterion, _resolve_category_id(db, category))


@app.get("/api/bias")
def api_bias(db: Session = Depends(get_db)):
    return service.compute_bias(db)


@app.get("/significance", response_class=HTMLResponse)
def significance_page(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str = "overall",
    category: str = "all",
):
    sig = service.compute_significance(db, criterion, _resolve_category_id(db, category))
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
        },
    )


# ------------------------------------------------------------------------ tasks


@app.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, db: Session = Depends(get_db)):
    tasks = db.execute(select(Task)).scalars().all()
    rows = []
    for t in tasks:
        rows.append(
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt,
                "category": t.category.name,
                "n_outputs": len(t.outputs),
                "active": t.active,
            }
        )
    return templates.TemplateResponse(request, "tasks.html", {"tasks": rows})


@app.get("/spotlight", response_class=HTMLResponse)
def spotlight_index(request: Request):
    from . import spotlight

    subjects = sorted(spotlight.SPOTLIGHTS, key=lambda s: (not s["featured"], s["order"]))
    return templates.TemplateResponse(request, "spotlight_index.html", {"subjects": subjects})


@app.get("/spotlight/{slug}", response_class=HTMLResponse)
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


def _default_benchmark_task_id(db: Session) -> int | None:
    """Return the first task_id that has a ReconTask row AND at least one ok Metric.

    Falls back to tasks[0].id if no scored task exists yet. Returns None if no tasks.
    Used by benchmark_page to avoid defaulting to an unscored task (P1-3 fix).
    """
    from .models import Metric, ReconTask, Task

    tasks = db.execute(select(Task)).scalars().all()
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


@app.get("/benchmark", response_class=HTMLResponse)
def benchmark_page(request: Request, db: Session = Depends(get_db), task_id: int | None = None):
    from . import recon_service
    from .models import Task

    tasks = db.execute(select(Task)).scalars().all()
    if task_id is None and tasks:
        task_id = _default_benchmark_task_id(db)
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


@app.get("/api/benchmark")
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
def export_dataset(db: Session = Depends(get_db)):
    """Reproducible research export: every decided comparison with full provenance.

    Generators are revealed here (post-hoc), enabling offline ranking studies.
    """
    rows = db.execute(
        select(Vote, Comparison).join(Comparison, Vote.comparison_id == Comparison.id)
    ).all()
    records = []
    for vote, comp in rows:
        out_a = db.get(ModelOutput, comp.output_a_id)
        out_b = db.get(ModelOutput, comp.output_b_id)
        task = db.get(Task, comp.task_id)
        crit = db.get(Criterion, comp.criterion_id)
        records.append(
            {
                "comparison_id": comp.id,
                "task": task.title,
                "category": task.category.slug,
                "criterion": crit.slug,
                "generator_a": db.get(Generator, out_a.generator_id).slug,
                "generator_b": db.get(Generator, out_b.generator_id).slug,
                "asset_a": out_a.asset_path,
                "asset_b": out_b.asset_path,
                "winner": vote.winner,  # a | b | tie | bad
                "session": vote.session_id,
                "voted_at": vote.created.isoformat(),
            }
        )
    return {"n_votes": len(records), "votes": records}


@app.get("/difficulty", response_class=HTMLResponse)
def difficulty_page(request: Request, db: Session = Depends(get_db)):
    """Render the per-tier objective scorecard + the cross-tier degradation gradient."""
    from .models import ReconTask, Task, TaskDifficulty

    scorecard = difficulty.tier_scorecard(db)
    tiers = list(difficulty.TIERS)

    # Species sitting in each tier (for the per-tier header line).
    tier_species: dict[str, list[str]] = {}
    rows = db.execute(
        select(TaskDifficulty, Task, ReconTask)
        .join(Task, Task.id == TaskDifficulty.task_id)
        .join(ReconTask, ReconTask.task_id == TaskDifficulty.task_id, isouter=True)
    ).all()
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

    perceptual = service.tier_perceptual_ranking(db)

    return templates.TemplateResponse(
        request,
        "difficulty.html",
        {
            "scorecard": scorecard,
            "tier_species": tier_species,
            "tiers": tiers,
            "gradient": gradient,
            "perceptual": perceptual,
        },
    )


@app.get("/api/difficulty.json")
def api_difficulty(db: Session = Depends(get_db)):
    """Per-(difficulty-tier × generator) objective scorecard over existing metrics."""
    return {"scorecard": difficulty.tier_scorecard(db)}


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
    if not integrity.verify_captcha(x_captcha_token):
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
    return templates.TemplateResponse(request, "moderation.html", {"pending": rows})


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


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": app.version}
