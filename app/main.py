"""FastAPI application — arena, voting, leaderboard, tasks, and admin tools."""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, ingest, integrity, matchmaking, ranking, service, submissions
from .database import get_db, init_db
from .models import (
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Rating,
    Task,
    Vote,
)
from .schemas import CategoryIn, GeneratorIn, TaskIn, VoteIn
from .storage import get_storage

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

    category_id = _resolve_category_id(db, category_slug)
    task = matchmaking.pick_task(db, category_id=category_id)
    if task is None:
        return None
    pair = matchmaking.pick_pair(db, task)
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


def _require_admin(token: str | None) -> None:
    if not token or token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


def require_admin_header(x_admin_token: str | None = Header(default=None)) -> None:
    """Dependency for programmatic JSON/upload endpoints (token via X-Admin-Token)."""
    _require_admin(x_admin_token)


# ------------------------------------------------------------------- arena UI


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "arena.html")


@app.get("/api/meta")
def api_meta(db: Session = Depends(get_db)):
    """Categories + criteria for populating arena/leaderboard selectors."""
    cats = db.execute(select(Category)).scalars().all()
    crits = db.execute(select(Criterion)).scalars().all()
    return {
        "categories": [{"slug": c.slug, "name": c.name} for c in cats],
        "criteria": [{"slug": c.slug, "name": c.name} for c in crits],
    }


@app.get("/api/next")
def api_next(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
):
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
    rows = []
    for r in ratings:
        gen = db.get(Generator, r.generator_id)
        rows.append(
            {
                "generator": gen.name,
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
    category_options = [{"slug": "all", "name": "All categories", "selected": category == "all"}]
    category_options += [
        {"slug": c.slug, "name": c.name, "selected": category == c.slug} for c in cats
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
    category_options = [{"slug": "all", "name": "All categories", "selected": category == "all"}]
    category_options += [
        {"slug": c.slug, "name": c.name, "selected": category == c.slug} for c in cats
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


# ------------------------------------------------------------------------ admin


@app.get("/admin", response_class=HTMLResponse)
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


@app.get("/admin/moderation", response_class=HTMLResponse)
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
    return RedirectResponse("/admin/moderation", status_code=303)


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
    return RedirectResponse("/admin/moderation", status_code=303)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": app.version}
