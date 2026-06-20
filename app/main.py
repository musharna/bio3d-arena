"""FastAPI application — arena, voting, leaderboard, tasks, and admin tools."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, matchmaking, service
from .database import get_db, init_db
from .models import Category, Comparison, Criterion, Generator, ModelOutput, Rating, Task, Vote
from .schemas import VoteIn

config.ensure_dirs()
init_db()

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="Bio 3D Arena", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
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


def _build_comparison(db: Session, session_id: str) -> dict | None:
    """Pick a task + pair, persist a Comparison row, return an anonymized payload."""
    task = matchmaking.pick_task(db)
    if task is None:
        return None
    pair = matchmaking.pick_pair(db, task)
    if pair is None:
        return None
    out_a, out_b = pair
    crit = _default_criterion(db)
    comparison = Comparison(
        task_id=task.id,
        output_a_id=out_a.id,
        output_b_id=out_b.id,
        criterion_id=crit.id,
        session_id=session_id,
    )
    db.add(comparison)
    db.commit()
    return {
        "comparison_id": comparison.id,
        "task": {"title": task.title, "prompt": task.prompt, "category": task.category.name},
        "criterion": {"slug": crit.slug, "name": crit.name},
        # Anonymized: never leak generator identity during voting.
        "a": {"url": f"/assets/{out_a.asset_path}", "format": out_a.asset_format},
        "b": {"url": f"/assets/{out_b.asset_path}", "format": out_b.asset_format},
    }


def _require_admin(token: str | None) -> None:
    if not token or token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


# ------------------------------------------------------------------- arena UI


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "arena.html")


@app.get("/api/next")
def api_next(request: Request, db: Session = Depends(get_db)):
    payload = _build_comparison(db, request.state.session_id)
    if payload is None:
        return JSONResponse({"error": "no-comparisons-available"}, status_code=404)
    return payload


@app.post("/api/vote")
def api_vote(vote_in: VoteIn, request: Request, db: Session = Depends(get_db)):
    comparison = db.get(Comparison, vote_in.comparison_id)
    if comparison is None:
        raise HTTPException(404, "Unknown comparison")
    if comparison.vote is not None:
        raise HTTPException(409, "Comparison already voted")
    vote = Vote(
        comparison_id=comparison.id,
        winner=vote_in.winner,
        session_id=request.state.session_id,
    )
    db.add(vote)
    db.flush()
    service.apply_vote(db, vote)
    db.commit()
    nxt = _build_comparison(db, request.state.session_id)
    return {"status": "ok", "next": nxt}


# ------------------------------------------------------------------ leaderboard


def _leaderboard_rows(db: Session, criterion_slug: str = "overall") -> list[dict]:
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    ratings = (
        db.execute(
            select(Rating).where(Rating.criterion_id == crit.id, Rating.category_id.is_(None))
        )
        .scalars()
        .all()
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
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request, db: Session = Depends(get_db)):
    rows = _leaderboard_rows(db)
    total = matchmaking.total_votes(db)
    return templates.TemplateResponse(
        request, "leaderboard.html", {"rows": rows, "total_votes": total}
    )


@app.get("/api/leaderboard")
def api_leaderboard(db: Session = Depends(get_db), criterion: str = "overall"):
    return {"criterion": criterion, "rows": _leaderboard_rows(db, criterion)}


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
    rel = Path("uploads") / f"{uuid.uuid4().hex}.{ext}"
    dest = config.ASSET_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    db.add(
        ModelOutput(
            task_id=task_id,
            generator_id=generator_id,
            title=title,
            asset_path=str(rel).replace("\\", "/"),
            asset_format=ext,
            meta_json=json.dumps({"uploaded": True, "filename": file.filename}),
        )
    )
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/recompute")
def admin_recompute(token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    result = {}
    for crit in db.execute(select(Criterion)).scalars().all():
        result[crit.slug] = service.recompute_leaderboard(db, crit.slug)
    return JSONResponse({"status": "recomputed", "detail": result})


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": app.version}
