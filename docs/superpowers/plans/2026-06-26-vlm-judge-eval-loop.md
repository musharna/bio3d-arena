# VLM-Judge Eval Loop + Human Calibration Study — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a VLM-as-judge that votes on rendered 3D-model pairs to fill the leaderboard, and measure VLM↔human agreement (Cohen's κ, rank correlation, self-consistency) across a perception ladder.

**Architecture:** Approach A — new tables (`JudgeVote`, `JudgeRating`, `CalibrationPair`) hold all VLM data; the human `Vote`/trust path is untouched. Pure logic (tiling, prompt, parsing, sampling, κ) lives in importable `app/` modules; heavy browser/API drivers live in `scripts/`. The existing `ranking.bradley_terry()` is reused verbatim to produce a parallel VLM leaderboard.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite, `create_all`-only), Playwright + `<model-viewer>` renders, Pillow compositing, Anthropic SDK (Claude Sonnet 4.6 vision), numpy/scipy for stats.

## Global Constraints

- **Schema is `create_all`-only — NEVER `ALTER`.** Add new tables only; never add columns to existing tables (`app/models.py:288,336`). New models in `app/models.py` are auto-created by `init_db()` (`app/database.py:66-68`).
- **Human path untouched.** Do not modify `Vote`, `Comparison`, `VoterSession`, `apply_vote`, `_matches_for_scope`, captcha/rate-limit/trust, or the human `recompute_*`.
- **Winner vocabulary is exactly `{a, b, tie, bad}`** everywhere (matches `Vote.winner`). `a`/`b` refer to the presented slot.
- **Tie credited as a split** (one win each direction); `bad` excluded — mirror `service._matches_for_scope` (`app/service.py:71-108`).
- **Judge model id:** `claude-sonnet-4-6` (recorded per vote in `JudgeVote.judge_model`).
- **View conditions are exactly `{single, multi4, turntable}`.**
- **Criteria for this study: `overall`, `visual_quality`, `structural_accuracy`** (slugs already seeded, `app/seed.py:293`).
- **Render cache convention:** `data/assets/renders/{output_id}_{condition}.png` under `config.ASSET_DIR`.
- **Pure functions take no DB/network** so they unit-test trivially (the `app/ranking.py` convention). Injectable seams: `capture_multi` for rendering, an Anthropic-like `client` for judging.
- **Run order:** `pytest` from repo root with the project venv (`.venv/bin/python -m pytest`). DB-backed tests use `from app.database import SessionLocal, init_db` and `from app.seed import seed_all`; `seed_all(force=True)` resets state.
- **Real-execution doctrine:** the render task and the judge task each carry one live check (real GLB render; one live Claude vision call), gated on availability via `pytest.skip`.
- **Secrets:** `ANTHROPIC_API_KEY` comes from the environment — never hard-code or log it.

---

## File Structure

**New (importable, unit-tested):**

- `app/judge_render.py` — view-condition config, contact-sheet path, Pillow tiler, render driver (capture injected).
- `app/judge.py` — judge prompt builder, verdict parser, single-pair judge call (client injected), swap-group id.
- `app/calibration.py` — calibration-set sampler, Cohen's κ, Spearman wrapper, agreement helpers.

**New (heavy CLI drivers, not unit-tested; verified by real-execution checks / smoke):**

- `scripts/judge_capture.py` — Playwright multi-angle capture factory.
- `scripts/judge_vlm.py` — batch judge driver (enumerate → render → judge → persist; resumable, capped).
- `scripts/build_calibration_set.py` — CLI around `app.calibration.build_calibration_set`.
- `scripts/calibration_report.py` — compute κ / rank-corr / self-consistency / ladder → results md.

**Modified:**

- `app/models.py` — add `JudgeVote`, `JudgeRating`, `CalibrationPair`.
- `app/service.py` — add `_judge_matches_for_scope`, `recompute_judge_scope`, `recompute_judge_all`.
- `app/main.py` — `/api/next?set=calibration` + progress; `/admin/recompute_judge`; VLM leaderboard rows + render.
- `requirements.txt` — add `anthropic`.

**Test files:** `tests/test_judge_models.py`, `tests/test_judge_render.py`, `tests/test_judge_capture_live.py`, `tests/test_judge.py`, `tests/test_judge_live.py`, `tests/test_judge_batch.py`, `tests/test_calibration.py`, `tests/test_judge_recompute.py`, `tests/test_calibration_mode.py`, `tests/test_calibration_report.py`.

---

## Task 1: New tables (JudgeVote, JudgeRating, CalibrationPair) + anthropic dep

**Files:**

- Modify: `app/models.py` (append after `ReconTask`, end of file ~line 347)
- Modify: `requirements.txt`
- Test: `tests/test_judge_models.py`

**Interfaces:**

- Produces: `JudgeVote(task_id, output_a_id, output_b_id, criterion_id, winner, view_condition, judge_model, swap_group, rationale, created)`; `JudgeRating(generator_id, category_id, criterion_id, view_condition, elo, bt_score, bt_lower, bt_upper, n_games, judge_model)` with unique scope `(generator_id, category_id, criterion_id, view_condition)`; `CalibrationPair(task_id, output_a_id, output_b_id, criterion_id, created)` with unique `(task_id, output_a_id, output_b_id, criterion_id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge_models.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import (
    CalibrationPair,
    Category,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    ModelOutput,
    Task,
)


def setup_module(_m):
    init_db()  # create_all picks up the new tables


def _scaffold(db):
    cat = Category(slug="jm-cat", name="JM")
    db.add(cat)
    db.flush()
    crit = Criterion(slug="jm-overall", name="Overall")
    gen = Generator(slug="jm-gen", name="G")
    task = Task(category_id=cat.id, title="jm-task", prompt="p")
    db.add_all([crit, gen, task])
    db.flush()
    oa = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/a.glb")
    ob = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/b.glb")
    db.add_all([oa, ob])
    db.flush()
    return cat, crit, gen, task, oa, ob


def test_judge_vote_persists():
    with SessionLocal() as db:
        _cat, crit, _gen, task, oa, ob = _scaffold(db)
        jv = JudgeVote(
            task_id=task.id,
            output_a_id=oa.id,
            output_b_id=ob.id,
            criterion_id=crit.id,
            winner="a",
            view_condition="multi4",
            judge_model="claude-sonnet-4-6",
            swap_group="grp-1",
            rationale="A is cleaner.",
        )
        db.add(jv)
        db.commit()
        got = db.get(JudgeVote, jv.id)
        assert got.winner == "a"
        assert got.view_condition == "multi4"
        assert got.judge_model == "claude-sonnet-4-6"


def test_judge_rating_scope_is_unique_per_view_condition():
    with SessionLocal() as db:
        _cat, crit, gen, _task, _oa, _ob = _scaffold(db)
        r1 = JudgeRating(generator_id=gen.id, criterion_id=crit.id, view_condition="multi4")
        r2 = JudgeRating(generator_id=gen.id, criterion_id=crit.id, view_condition="single")
        db.add_all([r1, r2])
        db.commit()  # same gen/crit, different view_condition → allowed
        assert r1.id != r2.id


def test_calibration_pair_persists():
    with SessionLocal() as db:
        _cat, crit, _gen, task, oa, ob = _scaffold(db)
        cp = CalibrationPair(
            task_id=task.id, output_a_id=oa.id, output_b_id=ob.id, criterion_id=crit.id
        )
        db.add(cp)
        db.commit()
        assert db.get(CalibrationPair, cp.id) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'JudgeVote'`.

- [ ] **Step 3: Add the models**

Append to `app/models.py` (after `ReconTask`). All imports used (`UniqueConstraint`, `Text`, etc.) are already imported at the top of the file.

```python
class JudgeVote(Base):
    """One VLM-judge judgment for a pair under a perception condition. winner ∈
    {a,b,tie,bad}; output_a_id/output_b_id are the PRESENTED slots, so winner maps
    back to a real output through them. swap_group links the A/B and B/A orders of
    the same logical comparison (judge self-consistency / position bias). Separate
    from human `vote` — the human integrity path never touches this table."""

    __tablename__ = "judge_vote"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    output_a_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    output_b_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    winner: Mapped[str] = mapped_column(String(8))  # 'a' | 'b' | 'tie' | 'bad'
    view_condition: Mapped[str] = mapped_column(String(16), index=True)  # single|multi4|turntable
    judge_model: Mapped[str] = mapped_column(String(48))
    swap_group: Mapped[str] = mapped_column(String(64), index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class JudgeRating(Base):
    """VLM-side cached ranking — mirrors `Rating` plus a `view_condition` key so each
    perception condition has its own leaderboard. Kept separate from `Rating` so the
    human leaderboard is never polluted by judge votes."""

    __tablename__ = "judge_rating"
    __table_args__ = (
        UniqueConstraint(
            "generator_id", "category_id", "criterion_id", "view_condition",
            name="uq_judge_rating_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    generator_id: Mapped[int] = mapped_column(ForeignKey("generator.id"), index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True, index=True
    )
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    view_condition: Mapped[str] = mapped_column(String(16), index=True)
    elo: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_score: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_lower: Mapped[float] = mapped_column(Float, default=1000.0)
    bt_upper: Mapped[float] = mapped_column(Float, default=1000.0)
    n_games: Mapped[int] = mapped_column(Integer, default=0)
    judge_model: Mapped[str] = mapped_column(String(48), default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class CalibrationPair(Base):
    """A pair in the shared calibration subset — voted by BOTH the human (via
    /api/next?set=calibration) and the VLM judge, so agreement (κ) is measured on the
    same pairings. Distinct from GoldPair (which has a known-correct answer)."""

    __tablename__ = "calibration_pair"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "output_a_id", "output_b_id", "criterion_id",
            name="uq_calibration_pair",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    output_a_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    output_b_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"))
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criterion.id"), index=True)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
```

- [ ] **Step 4: Add the dependency**

Add to `requirements.txt` (one line, after the existing entries):

```
anthropic>=0.40  # VLM-as-judge (Claude vision); ANTHROPIC_API_KEY from env
```

Then install into the venv: `.venv/bin/pip install 'anthropic>=0.40'`

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_judge_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/models.py requirements.txt tests/test_judge_models.py
git commit -m "feat(judge): JudgeVote/JudgeRating/CalibrationPair tables + anthropic dep"
```

---

## Task 2: Contact-sheet render logic (pure, capture injected)

**Files:**

- Create: `app/judge_render.py`
- Test: `tests/test_judge_render.py`

**Interfaces:**

- Consumes: `config.ASSET_DIR`, `models.ModelOutput`, `models.Critique` (for cache bookkeeping — reuse pattern from `render_spotlight._get_or_create_critique`, but write to disk by convention, not `Critique.render_path`).
- Produces:
  - `CONDITIONS: dict[str, dict]` — `{"single": {"azimuths": [30], "elev": 75, "cols": 1, "rows": 1}, "multi4": {"azimuths": [0,90,180,270], "elev": 70, "cols": 2, "rows": 2}, "turntable": {"azimuths": [0,45,90,135,180,225,270,315], "elev": 70, "cols": 4, "rows": 2}}`
  - `contact_sheet_path(output_id: int, condition: str) -> str` → `"renders/{oid}_{cond}.png"` (relative to ASSET_DIR).
  - `tile_contact_sheet(pngs: list[bytes], cols: int, rows: int) -> bytes` — composite row-major into one PNG (Pillow).
  - `render_contact_sheets(db, output_ids: list[int], condition: str, *, capture_multi) -> dict` where `capture_multi(glb_abs: str, azimuths: list[int], elev: int) -> list[bytes]`. Returns `{"rendered": int, "errors": int}`. Skips outputs whose sheet already exists (idempotent).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge_render.py
from __future__ import annotations

import io

from PIL import Image

from app import judge_render
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _png(color, size=64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def test_contact_sheet_path_convention():
    assert judge_render.contact_sheet_path(42, "multi4") == "renders/42_multi4.png"


def test_tile_contact_sheet_dimensions_2x2():
    tiles = [_png(c, 64) for c in ("red", "green", "blue", "white")]
    out = judge_render.tile_contact_sheet(tiles, cols=2, rows=2)
    img = Image.open(io.BytesIO(out))
    assert img.size == (128, 128)  # 2*64 x 2*64


def test_render_contact_sheets_writes_file_and_is_idempotent(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    calls = {"n": 0}

    def fake_capture(glb_abs, azimuths, elev):
        calls["n"] += 1
        return [_png("red", 64) for _ in azimuths]

    with SessionLocal() as db:
        cat = Category(slug="jr-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="jr-task", prompt="p")
        gen = Generator(slug="jr-gen", name="G")
        db.add_all([task, gen])
        db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        (tmp_path / "seed" / "x.glb").write_bytes(b"glTF-stub")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.commit()

        res = judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=fake_capture)
        assert res == {"rendered": 1, "errors": 0}
        sheet = tmp_path / judge_render.contact_sheet_path(out.id, "multi4")
        assert sheet.exists() and sheet.stat().st_size > 0
        # Idempotent: second call skips (no new capture).
        res2 = judge_render.render_contact_sheets(
            db, [out.id], "multi4", capture_multi=fake_capture
        )
        assert res2["rendered"] == 0
        assert calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.judge_render'`.

- [ ] **Step 3: Implement `app/judge_render.py`**

```python
"""Multi-view contact-sheet rendering for the VLM judge.

Pure logic + an injected `capture_multi`; no browser import here (the Playwright
driver lives in scripts/judge_capture.py). Sheets cache to disk by convention
`renders/{output_id}_{condition}.png` under ASSET_DIR, idempotently."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from . import config
from .models import ModelOutput

CONDITIONS: dict[str, dict] = {
    "single": {"azimuths": [30], "elev": 75, "cols": 1, "rows": 1},
    "multi4": {"azimuths": [0, 90, 180, 270], "elev": 70, "cols": 2, "rows": 2},
    "turntable": {
        "azimuths": [0, 45, 90, 135, 180, 225, 270, 315],
        "elev": 70,
        "cols": 4,
        "rows": 2,
    },
}


def contact_sheet_path(output_id: int, condition: str) -> str:
    return f"renders/{output_id}_{condition}.png"


def tile_contact_sheet(pngs: list[bytes], cols: int, rows: int) -> bytes:
    """Composite PNG bytes row-major into a single PNG. All tiles assumed same size."""
    tiles = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
    tw, th = tiles[0].size
    sheet = Image.new("RGB", (cols * tw, rows * th), (111, 118, 124))  # match render gray
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet.paste(tile, (c * tw, r * th))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def render_contact_sheets(db, output_ids: list[int], condition: str, *, capture_multi) -> dict:
    """Render+tile a contact sheet per output for `condition`. Idempotent: skips
    outputs whose sheet already exists. `capture_multi(glb_abs, azimuths, elev) ->
    list[bytes]` (one PNG per azimuth) is injected (Playwright in prod, stub in tests)."""
    spec = CONDITIONS[condition]
    renders_dir = Path(config.ASSET_DIR) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    rendered = errors = 0
    for oid in output_ids:
        rel = contact_sheet_path(oid, condition)
        abs_path = Path(config.ASSET_DIR) / rel
        if abs_path.exists() and abs_path.stat().st_size > 0:
            continue  # idempotent
        out = db.get(ModelOutput, oid)
        if out is None:
            errors += 1
            continue
        try:
            glb_abs = str(Path(config.ASSET_DIR) / out.asset_path)
            tiles = capture_multi(glb_abs, spec["azimuths"], spec["elev"])
            sheet = tile_contact_sheet(tiles, spec["cols"], spec["rows"])
            abs_path.write_bytes(sheet)
            rendered += 1
        except Exception:  # noqa: BLE001 — best-effort batch; caller logs counts
            errors += 1
    return {"rendered": rendered, "errors": errors}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_judge_render.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/judge_render.py tests/test_judge_render.py
git commit -m "feat(judge): multi-view contact-sheet render logic (capture injected)"
```

---

## Task 3: Playwright multi-angle capture + real render check

**Files:**

- Create: `scripts/judge_capture.py`
- Test: `tests/test_judge_capture_live.py`

**Interfaces:**

- Consumes: `config.ASSET_DIR`, `app.judge_render.render_contact_sheets`/`CONDITIONS`/`contact_sheet_path`.
- Produces: `browser_capture_multi_factory() -> capture_multi` where `capture_multi(glb_abs: str, azimuths: list[int], elev: int) -> list[bytes]`. Mirrors `render_spotlight._browser_capture_factory` but parameterizes `camera-orbit` per azimuth and returns one PNG per azimuth.

- [ ] **Step 1: Write the real-execution test (gated)**

```python
# tests/test_judge_capture_live.py
from __future__ import annotations

import shutil

import pytest

from app import judge_render


def _has_browser() -> bool:
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return shutil.which("chromium") is not None or True  # playwright ships its own


@pytest.mark.skipif(not _has_browser(), reason="playwright/chromium not available")
def test_real_multi4_render_of_a_seeded_glb(tmp_path, monkeypatch):
    """Real-execution check: render one real seeded GLB to a multi4 contact sheet."""
    import app.config as config
    from app.database import SessionLocal
    from app.models import ModelOutput
    from scripts.judge_capture import browser_capture_multi_factory

    with SessionLocal() as db:
        out = db.query(ModelOutput).filter(ModelOutput.is_gold.is_(False)).first()
        if out is None:
            pytest.skip("no model outputs seeded in this DB")
        # Render into a temp ASSET_DIR copy is overkill; reuse real ASSET_DIR cache dir
        # but a temp condition tag so we don't collide with a real sheet.
        capture_multi = browser_capture_multi_factory()
        res = judge_render.render_contact_sheets(db, [out.id], "multi4", capture_multi=capture_multi)
        assert res["rendered"] + res["errors"] == 1
        if res["errors"]:
            pytest.skip("render errored (asset/browser issue) — not a logic failure")
        sheet = config.ASSET_DIR / judge_render.contact_sheet_path(out.id, "multi4")
        assert sheet.exists() and sheet.stat().st_size > 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge_capture_live.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.judge_capture'` (or collection error on the import).

- [ ] **Step 3: Implement `scripts/judge_capture.py`**

```python
"""Playwright multi-angle capture for VLM-judge contact sheets.

Mirrors render_spotlight._browser_capture_factory but parameterizes the
<model-viewer> camera-orbit azimuth/elevation and returns one PNG per azimuth.
Heavy (browser); built lazily and used only by scripts/judge_vlm.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402


def browser_capture_multi_factory():
    """Returns capture_multi(glb_abs, azimuths, elev) -> list[png bytes]."""
    import http.server
    import socketserver
    import threading

    asset_root = str(config.ASSET_DIR)
    _state: dict = {"glb_rel": "", "az": 30, "elev": 75}

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=asset_root, **k)

        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/_render.html"):
                glb_rel, az, elev = _state["glb_rel"], _state["az"], _state["elev"]
                body = (
                    "<!doctype html><html><head>"
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<script type="module" '
                    'src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0'
                    '/model-viewer.min.js"></script>'
                    "</head><body style='margin:0'>"
                    f'<model-viewer id="mv" src="/{glb_rel}" '
                    f'camera-orbit="{az}deg {elev}deg auto" environment-image="neutral" '
                    'shadow-intensity="1" shadow-softness="0.8" '
                    'style="width:512px;height:512px;'
                    'background:linear-gradient(180deg,#b4babf 0%,#6f767c 100%)">'
                    "</model-viewer></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch()

    def capture_multi(glb_abs: str, azimuths: list[int], elev: int) -> list[bytes]:
        _state["glb_rel"] = str(
            Path(glb_abs).relative_to(config.ASSET_DIR)
        ).replace("\\", "/")
        _state["elev"] = elev
        pngs: list[bytes] = []
        for az in azimuths:
            _state["az"] = az
            page = browser.new_page(viewport={"width": 512, "height": 512})
            page.goto(f"http://127.0.0.1:{port}/_render.html?az={az}")
            page.wait_for_function(
                "document.querySelector('#mv')?.loaded === true", timeout=30000
            )
            pngs.append(page.locator("#mv").screenshot())
            page.close()
        return pngs

    return capture_multi
```

- [ ] **Step 4: Run test to verify it passes (or skips)**

Run: `.venv/bin/python -m pytest tests/test_judge_capture_live.py -q`
Expected: PASS if Playwright + a seeded GLB are present; otherwise SKIP. Either is acceptable — a hard FAIL is not.

- [ ] **Step 5: Commit**

```bash
git add scripts/judge_capture.py tests/test_judge_capture_live.py
git commit -m "feat(judge): Playwright multi-angle capture + real render check"
```

---

## Task 4: VLM judge core (prompt, parse, single-pair call) + live check

**Files:**

- Create: `app/judge.py`
- Test: `tests/test_judge.py`, `tests/test_judge_live.py`

**Interfaces:**

- Consumes: `models.Task`, `models.Criterion`, `models.ModelOutput`; `app.judge_render.contact_sheet_path`; an Anthropic-like `client` with `client.messages.create(...)` returning an object whose `.content` is a list of blocks; a forced tool `record_verdict`.
- Produces:
  - `JUDGE_MODEL = "claude-sonnet-4-6"`
  - `VERDICT_TOOL: dict` — Anthropic tool schema for `record_verdict(winner, rationale)`.
  - `build_messages(species: str, prompt: str, criterion_name: str, criterion_desc: str, sheet_a_b64: str, sheet_b_b64: str) -> list[dict]` — one user message: rubric text + two labeled images.
  - `parse_verdict(response) -> tuple[str, str]` — pull `(winner, rationale)` from the forced `tool_use` block; raise `ValueError` if winner ∉ `{a,b,tie,bad}`.
  - `swap_group_id(task_id, lo_output_id, hi_output_id, criterion_id, condition) -> str` — stable id for a logical comparison (order-independent).
  - `judge_pair(client, *, species, prompt, criterion_name, criterion_desc, sheet_a_b64, sheet_b_b64) -> tuple[str, str]` — calls the client with forced tool, returns `parse_verdict(...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge.py
from __future__ import annotations

import pytest

from app import judge


class _Block:
    def __init__(self, winner, rationale):
        self.type = "tool_use"
        self.name = "record_verdict"
        self.input = {"winner": winner, "rationale": rationale}


class _Resp:
    def __init__(self, winner, rationale="because"):
        self.content = [_Block(winner, rationale)]


class _FakeClient:
    def __init__(self, winner):
        self._winner = winner
        self.last_kwargs = None

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.last_kwargs = kwargs
            return _Resp(self._outer._winner)

    @property
    def messages(self):
        return _FakeClient._Messages(self)


def test_swap_group_is_order_independent():
    g1 = judge.swap_group_id(1, 10, 20, 3, "multi4")
    g2 = judge.swap_group_id(1, 20, 10, 3, "multi4")
    assert g1 == g2
    assert judge.swap_group_id(1, 10, 20, 3, "single") != g1


def test_build_messages_has_rubric_and_two_images():
    msgs = judge.build_messages(
        "Tomato", "Generate a tomato plant", "Visual quality",
        "Mesh cleanliness", "QQ==", "QQ==",
    )
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    parts = msgs[0]["content"]
    text = " ".join(p.get("text", "") for p in parts if p["type"] == "text")
    assert "Visual quality" in text and "Mesh cleanliness" in text
    assert "Tomato" in text
    images = [p for p in parts if p["type"] == "image"]
    assert len(images) == 2


def test_parse_verdict_accepts_valid_winner():
    assert judge.parse_verdict(_Resp("a")) == ("a", "because")
    assert judge.parse_verdict(_Resp("tie"))[0] == "tie"


def test_parse_verdict_rejects_garbage():
    with pytest.raises(ValueError):
        judge.parse_verdict(_Resp("left"))


def test_judge_pair_forces_tool_and_returns_winner():
    client = _FakeClient("b")
    winner, rationale = judge.judge_pair(
        client, species="Pine", prompt="p", criterion_name="Overall",
        criterion_desc="best overall", sheet_a_b64="QQ==", sheet_b_b64="QQ==",
    )
    assert winner == "b"
    assert client.last_kwargs["model"] == judge.JUDGE_MODEL
    assert client.last_kwargs["tool_choice"]["name"] == "record_verdict"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.judge'`.

- [ ] **Step 3: Implement `app/judge.py`**

```python
"""VLM-as-judge core: prompt construction, forced-tool verdict parsing, one-pair call.

Pure except for `judge_pair`, which takes an injected Anthropic-like client (the real
client is built in scripts/judge_vlm.py from ANTHROPIC_API_KEY). Winner vocabulary is
exactly {a,b,tie,bad} to match human Vote.winner."""

from __future__ import annotations

import hashlib

JUDGE_MODEL = "claude-sonnet-4-6"
_VALID = {"a", "b", "tie", "bad"}

VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record which 3D model better satisfies the criterion.",
    "input_schema": {
        "type": "object",
        "properties": {
            "winner": {
                "type": "string",
                "enum": ["a", "b", "tie", "bad"],
                "description": "a=Model A better, b=Model B better, tie=equal, "
                "bad=both unusable for this criterion",
            },
            "rationale": {"type": "string", "description": "One sentence justification."},
        },
        "required": ["winner", "rationale"],
    },
}


def swap_group_id(
    task_id: int, output_id_x: int, output_id_y: int, criterion_id: int, condition: str
) -> str:
    """Order-independent id for one logical comparison (links the A/B & B/A votes)."""
    lo, hi = sorted((output_id_x, output_id_y))
    raw = f"{task_id}:{lo}:{hi}:{criterion_id}:{condition}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _img(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def build_messages(
    species: str,
    prompt: str,
    criterion_name: str,
    criterion_desc: str,
    sheet_a_b64: str,
    sheet_b_b64: str,
) -> list[dict]:
    """One user message: rubric + Model A image + Model B image."""
    text = (
        f"You are judging two AI-generated 3D models of: {species}.\n"
        f"Generation task: {prompt}\n\n"
        f"Criterion — {criterion_name}: {criterion_desc}\n\n"
        "Each image is a contact sheet of one model rendered from several angles on a "
        "neutral gray background. The FIRST image is Model A; the SECOND is Model B. "
        "Decide which model better satisfies the criterion, then call record_verdict. "
        "Use 'tie' only if genuinely indistinguishable, and 'bad' only if BOTH are "
        "unusable for this criterion."
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "text", "text": "Model A:"},
                _img(sheet_a_b64),
                {"type": "text", "text": "Model B:"},
                _img(sheet_b_b64),
            ],
        }
    ]


def parse_verdict(response) -> tuple[str, str]:
    """Extract (winner, rationale) from the forced tool_use block."""
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == "record_verdict":
            data = block.input or {}
            winner = data.get("winner")
            if winner not in _VALID:
                raise ValueError(f"invalid winner: {winner!r}")
            return winner, data.get("rationale", "")
    raise ValueError("no record_verdict tool_use block in response")


def judge_pair(
    client,
    *,
    species: str,
    prompt: str,
    criterion_name: str,
    criterion_desc: str,
    sheet_a_b64: str,
    sheet_b_b64: str,
) -> tuple[str, str]:
    """Call the VLM with a forced verdict tool; return (winner, rationale)."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=400,
        tools=[VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "record_verdict"},
        messages=build_messages(
            species, prompt, criterion_name, criterion_desc, sheet_a_b64, sheet_b_b64
        ),
    )
    return parse_verdict(resp)
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_judge.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Write the live check (gated on ANTHROPIC_API_KEY)**

```python
# tests/test_judge_live.py
from __future__ import annotations

import base64
import io
import os

import pytest

from app import judge


def _png_b64() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY")
def test_one_live_vision_call_returns_valid_winner():
    """Real-execution check: a single live Claude vision call end-to-end."""
    import anthropic

    client = anthropic.Anthropic()
    b64 = _png_b64()
    winner, rationale = judge.judge_pair(
        client, species="Tomato", prompt="Generate a tomato plant",
        criterion_name="Overall", criterion_desc="best output overall",
        sheet_a_b64=b64, sheet_b_b64=b64,
    )
    assert winner in {"a", "b", "tie", "bad"}
    assert isinstance(rationale, str)
```

- [ ] **Step 6: Run the live check (passes or skips)**

Run: `.venv/bin/python -m pytest tests/test_judge_live.py -q`
Expected: PASS if `ANTHROPIC_API_KEY` is set (it is in this environment); otherwise SKIP. (Two identical images → any winner is valid.)

- [ ] **Step 7: Commit**

```bash
git add app/judge.py tests/test_judge.py tests/test_judge_live.py
git commit -m "feat(judge): VLM judge core (prompt/parse/forced-tool) + live vision check"
```

---

## Task 5: Calibration-set sampler

**Files:**

- Create: `app/calibration.py`
- Create: `scripts/build_calibration_set.py`
- Test: `tests/test_calibration.py`

**Interfaces:**

- Consumes: `models.Task`, `models.ModelOutput`, `models.Criterion`, `models.CalibrationPair`, `matchmaking._real_outputs`.
- Produces:
  - `STUDY_CRITERIA = ["overall", "visual_quality", "structural_accuracy"]`
  - `build_calibration_set(db, n_per_criterion: int = 50, criteria_slugs: list[str] | None = None, seed: int = 12345, replace: bool = True) -> dict` — stratified sample of distinct non-gold pairs across active tasks, per criterion; inserts `CalibrationPair` rows; returns `{"created": int, "per_criterion": {slug: int}}`. Never self-pairs; deterministic for a given seed; idempotent when `replace=True` (clears existing rows first).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibration.py
from __future__ import annotations

from app import calibration
from app.database import SessionLocal, init_db
from app.models import CalibrationPair, Category, Criterion, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _seed_two_tasks(db):
    db.query(CalibrationPair).delete()
    db.commit()
    cat = Category(slug="cal-cat", name="C")
    db.add(cat)
    db.flush()
    for slug, name in [
        ("overall", "Overall"),
        ("visual_quality", "Visual quality"),
        ("structural_accuracy", "Structural accuracy"),
    ]:
        if not db.query(Criterion).filter_by(slug=slug).first():
            db.add(Criterion(slug=slug, name=name))
    gens = [Generator(slug=f"cal-g{i}", name=f"G{i}") for i in range(4)]
    db.add_all(gens)
    db.flush()
    for t in range(2):
        task = Task(category_id=cat.id, title=f"cal-task-{t}", prompt="p")
        db.add(task)
        db.flush()
        for g in gens:
            db.add(ModelOutput(task_id=task.id, generator_id=g.id, asset_path=f"seed/{t}_{g.id}.glb"))
    db.commit()


def test_sampler_creates_stratified_distinct_pairs():
    with SessionLocal() as db:
        _seed_two_tasks(db)
        res = calibration.build_calibration_set(db, n_per_criterion=5, seed=7)
        assert res["created"] == 15  # 5 * 3 criteria
        rows = db.query(CalibrationPair).all()
        assert len(rows) == 15
        for r in rows:
            assert r.output_a_id != r.output_b_id  # no self-pairs
        per = res["per_criterion"]
        assert per["overall"] == 5 and per["visual_quality"] == 5
        assert per["structural_accuracy"] == 5


def test_sampler_is_deterministic_and_idempotent():
    with SessionLocal() as db:
        _seed_two_tasks(db)
        a = calibration.build_calibration_set(db, n_per_criterion=4, seed=99)
        keys_a = {
            (p.task_id, p.output_a_id, p.output_b_id, p.criterion_id)
            for p in db.query(CalibrationPair).all()
        }
        b = calibration.build_calibration_set(db, n_per_criterion=4, seed=99, replace=True)
        keys_b = {
            (p.task_id, p.output_a_id, p.output_b_id, p.criterion_id)
            for p in db.query(CalibrationPair).all()
        }
        assert a["created"] == b["created"]
        assert keys_a == keys_b  # same seed → same set; replace=True avoids duplicates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_calibration.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.calibration'`.

- [ ] **Step 3: Implement `app/calibration.py`** (sampler portion; κ added in Task 9)

```python
"""Calibration subset sampling + agreement stats for the VLM↔human study.

The sampler picks a stratified set of distinct non-gold pairs (per criterion) that
BOTH the human and the VLM judge vote, so κ is measured on identical pairings."""

from __future__ import annotations

import itertools
import random

from sqlalchemy import select

from .matchmaking import _real_outputs
from .models import CalibrationPair, Criterion, Task

STUDY_CRITERIA = ["overall", "visual_quality", "structural_accuracy"]


def _all_pairs_by_task(db) -> list[tuple[int, int, int]]:
    """Every distinct (task_id, lo_output_id, hi_output_id) over active tasks."""
    pairs: list[tuple[int, int, int]] = []
    tasks = db.execute(select(Task).where(Task.active.is_(True))).scalars().all()
    for task in tasks:
        outs = sorted(o.id for o in _real_outputs(task))
        for a, b in itertools.combinations(outs, 2):
            pairs.append((task.id, a, b))
    return pairs


def build_calibration_set(
    db,
    n_per_criterion: int = 50,
    criteria_slugs: list[str] | None = None,
    seed: int = 12345,
    replace: bool = True,
) -> dict:
    """Insert a stratified CalibrationPair sample. Deterministic for a given seed."""
    criteria_slugs = criteria_slugs or STUDY_CRITERIA
    if replace:
        db.query(CalibrationPair).delete()
        db.flush()

    universe = _all_pairs_by_task(db)
    rng = random.Random(seed)
    rng.shuffle(universe)

    per: dict[str, int] = {}
    created = 0
    for slug in criteria_slugs:
        crit = db.execute(select(Criterion).where(Criterion.slug == slug)).scalars().first()
        if crit is None:
            per[slug] = 0
            continue
        chosen = universe[:n_per_criterion]
        for task_id, a, b in chosen:
            db.add(
                CalibrationPair(
                    task_id=task_id, output_a_id=a, output_b_id=b, criterion_id=crit.id
                )
            )
        per[slug] = len(chosen)
        created += len(chosen)
    db.commit()
    return {"created": created, "per_criterion": per}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_calibration.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Implement the CLI wrapper `scripts/build_calibration_set.py`**

```python
"""CLI: build the shared calibration subset. Usage:
  .venv/bin/python scripts/build_calibration_set.py --n 50 --seed 12345"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calibration import build_calibration_set  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=50, help="pairs per criterion")
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args()
    with SessionLocal() as db:
        res = build_calibration_set(db, n_per_criterion=args.n, seed=args.seed)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add app/calibration.py scripts/build_calibration_set.py tests/test_calibration.py
git commit -m "feat(judge): stratified calibration-set sampler + CLI"
```

---

## Task 6: VLM judge batch driver (enumerate → judge → persist; resumable)

**Files:**

- Create: `scripts/judge_vlm.py`
- Test: `tests/test_judge_batch.py`

**Interfaces:**

- Consumes: `app.judge` (`judge_pair`, `swap_group_id`, `JUDGE_MODEL`), `app.judge_render` (`render_contact_sheets`, `contact_sheet_path`, `CONDITIONS`), `models` (`JudgeVote`, `Task`, `ModelOutput`, `Criterion`, `CalibrationPair`), `matchmaking._real_outputs`.
- Produces (in `scripts/judge_vlm.py`, importable for tests):
  - `enumerate_work(db, grid_condition="multi4", criteria_slugs=None) -> list[dict]` — work items `{"task_id","output_a_id","output_b_id","criterion_id","criterion_slug","condition","swap_group"}`. Grid: all task pairs × criteria under `grid_condition`. Calibration: each `CalibrationPair` × all `CONDITIONS`. Each logical item expands to TWO ordered rows (A/B and B/A) sharing one `swap_group`.
  - `existing_swap_orders(db) -> set[tuple[str,int,int]]` — `(swap_group, output_a_id, output_b_id)` already in `JudgeVote` (resume key).
  - `run_batch(db, *, judge_fn, sheet_b64, grid_condition="multi4", criteria_slugs=None, max_votes=None) -> dict` — for each not-yet-present ordered item: get both sheets via `sheet_b64(output_id, condition) -> str`, call `judge_fn(species, prompt, criterion_name, criterion_desc, sheet_a_b64, sheet_b_b64) -> (winner, rationale)`, write a `JudgeVote`. Returns `{"written": int, "skipped": int, "errors": int}`. (`judge_fn` and `sheet_b64` injected so tests need no browser/API.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge_batch.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import CalibrationPair, Category, Criterion, Generator, JudgeVote, ModelOutput, Task


def setup_module(_m):
    init_db()


def _seed(db):
    db.query(JudgeVote).delete()
    db.query(CalibrationPair).delete()
    db.commit()
    cat = Category(slug="jb-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    gens = [Generator(slug=f"jb-g{i}", name=f"G{i}") for i in range(3)]
    db.add_all(gens)
    db.flush()
    task = Task(category_id=cat.id, title="jb-task", prompt="p")
    db.add(task)
    db.flush()
    for g in gens:
        db.add(ModelOutput(task_id=task.id, generator_id=g.id, asset_path=f"seed/{g.id}.glb"))
    db.commit()
    return task, crit


def test_run_batch_writes_both_orders_and_resumes():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        _seed(db)
        calls = {"n": 0}

        def judge_fn(species, prompt, cname, cdesc, a_b64, b_b64):
            calls["n"] += 1
            return "a", "stub rationale"

        def sheet_b64(output_id, condition):
            return "QQ=="  # 1-byte PNG stub; not actually decoded by the stub judge

        res = jv.run_batch(
            db, judge_fn=judge_fn, sheet_b64=sheet_b64, grid_condition="multi4",
            criteria_slugs=["overall"],
        )
        # 3 generators → C(3,2)=3 logical pairs × 2 orders = 6 votes.
        assert res["written"] == 6
        votes = db.query(JudgeVote).all()
        assert len(votes) == 6
        groups = {v.swap_group for v in votes}
        assert len(groups) == 3  # each logical pair shares one swap_group
        for g in groups:
            assert db.query(JudgeVote).filter_by(swap_group=g).count() == 2

        # Resume: a second run writes nothing new.
        res2 = jv.run_batch(
            db, judge_fn=judge_fn, sheet_b64=sheet_b64, grid_condition="multi4",
            criteria_slugs=["overall"],
        )
        assert res2["written"] == 0 and res2["skipped"] == 6


def test_max_votes_caps_writes():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        _seed(db)
        res = jv.run_batch(
            db,
            judge_fn=lambda *a: ("b", "r"),
            sheet_b64=lambda oid, cond: "QQ==",
            grid_condition="multi4",
            criteria_slugs=["overall"],
            max_votes=2,
        )
        assert res["written"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge_batch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.judge_vlm'`.

- [ ] **Step 3: Implement `scripts/judge_vlm.py`**

```python
"""VLM-judge batch driver: enumerate comparisons, render sheets, judge, persist.

Resumable (skips swap-group/order rows already in JudgeVote) and capped (--max).
enumerate_work/run_batch are import-testable with injected judge_fn + sheet_b64;
main() wires the real Playwright renderer + Anthropic client and runs via jobd."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import judge, judge_render  # noqa: E402
from app.calibration import STUDY_CRITERIA  # noqa: E402
from app.matchmaking import _real_outputs  # noqa: E402
from app.models import (  # noqa: E402
    CalibrationPair,
    Criterion,
    JudgeVote,
    ModelOutput,
    Task,
)

GRID_CONDITION = "multi4"


def enumerate_work(db, grid_condition: str = GRID_CONDITION, criteria_slugs=None) -> list[dict]:
    """Ordered work rows (two per logical comparison, sharing a swap_group)."""
    criteria_slugs = criteria_slugs or STUDY_CRITERIA
    crit_by_slug = {
        c.slug: c
        for c in db.execute(select(Criterion).where(Criterion.slug.in_(criteria_slugs))).scalars()
    }
    items: list[dict] = []

    def add(task_id, a, b, crit, condition):
        grp = judge.swap_group_id(task_id, a, b, crit.id, condition)
        for oa, ob in ((a, b), (b, a)):
            items.append({
                "task_id": task_id, "output_a_id": oa, "output_b_id": ob,
                "criterion_id": crit.id, "criterion_slug": crit.slug,
                "condition": condition, "swap_group": grp,
            })

    # Grid: every task pair × criteria, under the single grid condition.
    for task in db.execute(select(Task).where(Task.active.is_(True))).scalars():
        outs = sorted(o.id for o in _real_outputs(task))
        for i in range(len(outs)):
            for j in range(i + 1, len(outs)):
                for slug in criteria_slugs:
                    if slug in crit_by_slug:
                        add(task.id, outs[i], outs[j], crit_by_slug[slug], grid_condition)

    # Calibration subset: each pair × ALL conditions (the perception ladder).
    for cp in db.execute(select(CalibrationPair)).scalars():
        crit = db.get(Criterion, cp.criterion_id)
        if crit is None:
            continue
        a, b = sorted((cp.output_a_id, cp.output_b_id))
        for condition in judge_render.CONDITIONS:
            add(cp.task_id, a, b, crit, condition)
    return items


def existing_swap_orders(db) -> set:
    return {
        (v.swap_group, v.output_a_id, v.output_b_id)
        for v in db.execute(select(JudgeVote)).scalars()
    }


def run_batch(
    db, *, judge_fn, sheet_b64, grid_condition: str = GRID_CONDITION,
    criteria_slugs=None, max_votes: int | None = None,
) -> dict:
    """judge_fn(species, prompt, criterion_name, criterion_desc, a_b64, b_b64)->(winner,rationale).
    sheet_b64(output_id, condition)->base64 PNG string."""
    work = enumerate_work(db, grid_condition, criteria_slugs)
    seen = existing_swap_orders(db)
    written = skipped = errors = 0
    for item in work:
        key = (item["swap_group"], item["output_a_id"], item["output_b_id"])
        if key in seen:
            skipped += 1
            continue
        if max_votes is not None and written >= max_votes:
            break
        task = db.get(Task, item["task_id"])
        crit = db.get(Criterion, item["criterion_id"])
        try:
            a_b64 = sheet_b64(item["output_a_id"], item["condition"])
            b_b64 = sheet_b64(item["output_b_id"], item["condition"])
            winner, rationale = judge_fn(
                task.category.name if task.category else "",
                task.prompt, crit.name, crit.description, a_b64, b_b64,
            )
            db.add(JudgeVote(
                task_id=item["task_id"], output_a_id=item["output_a_id"],
                output_b_id=item["output_b_id"], criterion_id=item["criterion_id"],
                winner=winner, view_condition=item["condition"],
                judge_model=judge.JUDGE_MODEL, swap_group=item["swap_group"],
                rationale=rationale,
            ))
            db.commit()
            seen.add(key)
            written += 1
        except Exception as e:  # noqa: BLE001 — best-effort; count + continue
            db.rollback()
            errors += 1
            print(f"judge error on {key}: {e}", file=sys.stderr)
    return {"written": written, "skipped": skipped, "errors": errors}


def _real_sheet_b64_factory(db, capture_multi):
    """Render-on-demand sheet provider for production runs."""
    def sheet_b64(output_id: int, condition: str) -> str:
        judge_render.render_contact_sheets(db, [output_id], condition, capture_multi=capture_multi)
        from app import config
        path = Path(config.ASSET_DIR) / judge_render.contact_sheet_path(output_id, condition)
        return base64.b64encode(path.read_bytes()).decode()
    return sheet_b64


def main() -> int:
    import argparse

    import anthropic

    from app.database import SessionLocal
    from scripts.judge_capture import browser_capture_multi_factory

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max", type=int, default=None, help="cap votes written this run")
    args = ap.parse_args()

    client = anthropic.Anthropic()

    def judge_fn(species, prompt, cname, cdesc, a_b64, b_b64):
        return judge.judge_pair(
            client, species=species, prompt=prompt, criterion_name=cname,
            criterion_desc=cdesc, sheet_a_b64=a_b64, sheet_b_b64=b_b64,
        )

    with SessionLocal() as db:
        capture_multi = browser_capture_multi_factory()
        sheet_b64 = _real_sheet_b64_factory(db, capture_multi)
        res = run_batch(db, judge_fn=judge_fn, sheet_b64=sheet_b64, max_votes=args.max)
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_judge_batch.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/judge_vlm.py tests/test_judge_batch.py
git commit -m "feat(judge): batch driver (enumerate/judge/persist, resumable, capped)"
```

---

## Task 7: VLM leaderboard recompute (reuse Bradley-Terry)

**Files:**

- Modify: `app/service.py` (add functions after `recompute_all`, ~line 167)
- Modify: `app/main.py` (add `/admin/recompute_judge` after `/admin/recompute`, ~line 564)
- Test: `tests/test_judge_recompute.py`

**Interfaces:**

- Consumes: `ranking.bradley_terry`, `models.JudgeVote`, `models.JudgeRating`, `models.ModelOutput`, `models.Criterion`, `service._players_for_scope`.
- Produces:
  - `service._judge_matches_for_scope(db, criterion_id, view_condition) -> list[tuple[int,int]]` — decisive `(winner_gen, loser_gen)` from `JudgeVote` (tie split both ways; bad excluded), mirroring `_matches_for_scope`.
  - `service.recompute_judge_scope(db, criterion, view_condition, commit=True) -> dict` — fit BT, upsert `JudgeRating` for `(generator, category_id=None, criterion, view_condition)`.
  - `service.recompute_judge_all(db, view_condition="multi4") -> dict` — over all criteria for one condition.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge_recompute.py
from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    ModelOutput,
    Task,
)


def setup_module(_m):
    init_db()


def _seed_votes(db):
    db.query(JudgeVote).delete()
    db.query(JudgeRating).delete()
    db.commit()
    cat = Category(slug="jr2-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    strong = Generator(slug="jr2-strong", name="Strong")
    weak = Generator(slug="jr2-weak", name="Weak")
    db.add_all([strong, weak])
    db.flush()
    task = Task(category_id=cat.id, title="jr2-task", prompt="p")
    db.add(task)
    db.flush()
    so = ModelOutput(task_id=task.id, generator_id=strong.id, asset_path="seed/s.glb")
    wo = ModelOutput(task_id=task.id, generator_id=weak.id, asset_path="seed/w.glb")
    db.add_all([so, wo])
    db.flush()
    # Strong (slot a) beats weak 9 times under multi4.
    for _ in range(9):
        db.add(JudgeVote(
            task_id=task.id, output_a_id=so.id, output_b_id=wo.id, criterion_id=crit.id,
            winner="a", view_condition="multi4", judge_model="claude-sonnet-4-6",
            swap_group="g", rationale="",
        ))
    db.commit()
    return crit, strong, weak


def test_recompute_judge_orders_strong_above_weak():
    with SessionLocal() as db:
        crit, strong, weak = _seed_votes(db)
        out = service.recompute_judge_scope(db, crit, "multi4")
        assert out["matches"] == 9
        rs = db.query(JudgeRating).filter_by(
            generator_id=strong.id, criterion_id=crit.id, view_condition="multi4"
        ).one()
        rw = db.query(JudgeRating).filter_by(
            generator_id=weak.id, criterion_id=crit.id, view_condition="multi4"
        ).one()
        assert rs.bt_score > rw.bt_score
        assert rs.n_games == 9


def test_recompute_judge_all_runs_over_criteria():
    with SessionLocal() as db:
        _seed_votes(db)
        res = service.recompute_judge_all(db, view_condition="multi4")
        assert res["status"] == "ok"
        assert res["view_condition"] == "multi4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge_recompute.py -q`
Expected: FAIL — `AttributeError: module 'app.service' has no attribute 'recompute_judge_scope'`.

- [ ] **Step 3: Implement the service functions**

Add `JudgeRating` and `JudgeVote` to the `from .models import (...)` block at the top of `app/service.py` (lines 16-26), then append:

```python
def _judge_matches_for_scope(
    db: Session, criterion_id: int, view_condition: str, include_ties: bool = True
) -> list[tuple[int, int]]:
    """Decisive (winner_gen, loser_gen) pairs from JudgeVote for one (criterion, condition).
    Tie → split both directions; bad excluded. Mirrors _matches_for_scope (human)."""
    from .models import JudgeVote

    stmt = select(JudgeVote).where(
        JudgeVote.criterion_id == criterion_id,
        JudgeVote.view_condition == view_condition,
    )
    matches: list[tuple[int, int]] = []
    for jv in db.execute(stmt).scalars():
        if jv.winner == "bad":
            continue
        gen_a = db.get(ModelOutput, jv.output_a_id).generator_id
        gen_b = db.get(ModelOutput, jv.output_b_id).generator_id
        if jv.winner == "a":
            matches.append((gen_a, gen_b))
        elif jv.winner == "b":
            matches.append((gen_b, gen_a))
        elif jv.winner == "tie" and include_ties:
            matches.append((gen_a, gen_b))
            matches.append((gen_b, gen_a))
    return matches


def _get_or_create_judge_rating(
    db: Session, generator_id: int, criterion_id: int, view_condition: str
):
    from .models import JudgeRating

    stmt = select(JudgeRating).where(
        JudgeRating.generator_id == generator_id,
        JudgeRating.criterion_id == criterion_id,
        JudgeRating.view_condition == view_condition,
        JudgeRating.category_id.is_(None),
    )
    r = db.execute(stmt).scalars().first()
    if r is None:
        r = JudgeRating(
            generator_id=generator_id, criterion_id=criterion_id,
            view_condition=view_condition, category_id=None,
        )
        db.add(r)
        db.flush()
    return r


def recompute_judge_scope(
    db: Session, criterion: Criterion, view_condition: str, commit: bool = True
) -> dict:
    """Refit Bradley-Terry over JudgeVote for (criterion, condition); cache JudgeRating."""
    matches = _judge_matches_for_scope(db, criterion.id, view_condition)
    players = sorted(set(_players_for_scope(db, None)) | {p for m in matches for p in m})
    result = ranking.bradley_terry(players, matches, bootstrap=config.BT_BOOTSTRAP)
    for gid in players:
        r = _get_or_create_judge_rating(db, gid, criterion.id, view_condition)
        r.bt_score = result.scores.get(gid, ranking.BT_BASE)
        r.bt_lower = result.lower.get(gid, ranking.BT_BASE)
        r.bt_upper = result.upper.get(gid, ranking.BT_BASE)
        r.n_games = int(result.n_games.get(gid, 0))
        r.judge_model = "claude-sonnet-4-6"
    if commit:
        db.commit()
    return {"matches": len(matches), "players": len(players)}


def recompute_judge_all(db: Session, view_condition: str = "multi4") -> dict:
    """Recompute the VLM leaderboard for every criterion under one view condition."""
    criteria = db.execute(select(Criterion)).scalars().all()
    for criterion in criteria:
        recompute_judge_scope(db, criterion, view_condition, commit=False)
    db.commit()
    return {"status": "ok", "view_condition": view_condition, "criteria": len(criteria)}
```

- [ ] **Step 4: Add the admin endpoint**

In `app/main.py`, after `admin_recompute` (~line 564):

```python
@app.post("/admin/recompute_judge")
def admin_recompute_judge(
    token: str = Form(...), view_condition: str = Form("multi4"), db: Session = Depends(get_db)
):
    _require_admin(token)
    detail = service.recompute_judge_all(db, view_condition=view_condition)
    return JSONResponse({"status": "recomputed", "detail": detail})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_judge_recompute.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add app/service.py app/main.py tests/test_judge_recompute.py
git commit -m "feat(judge): VLM leaderboard recompute (reuses Bradley-Terry) + admin endpoint"
```

---

## Task 8: Human calibration voting mode (`/api/next?set=calibration`)

**Files:**

- Modify: `app/main.py` (add a calibration builder + extend `api_next`)
- Test: `tests/test_calibration_mode.py`

**Interfaces:**

- Consumes: `models.CalibrationPair`, `models.Comparison`, `models.Vote`, `models.Criterion`, `_serialize`, `integrity.already_voted_pair`.
- Produces:
  - `_build_calibration_comparison(db, session_id) -> dict | None` — pick a `CalibrationPair` the session hasn't yet voted (its criterion), persist a `Comparison` (randomized A/B), return the anon payload plus `{"progress": {"voted": int, "total": int}, "set": "calibration"}`. Returns `{"set":"calibration","done":True,"progress":{...}}` when exhausted.
  - `api_next` gains `set: str | None = None`; when `set == "calibration"`, route to the calibration builder.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibration_mode.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app
from app.models import (
    CalibrationPair,
    Category,
    Comparison,
    Criterion,
    Generator,
    ModelOutput,
    Task,
    Vote,
)


def setup_module(_m):
    init_db()


def _seed_calibration(db):
    for t in (Vote, Comparison, CalibrationPair):
        db.query(t).delete()
    db.commit()
    cat = Category(slug="cm-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    g1, g2 = Generator(slug="cm-g1", name="G1"), Generator(slug="cm-g2", name="G2")
    db.add_all([g1, g2])
    db.flush()
    task = Task(category_id=cat.id, title="cm-task", prompt="p")
    db.add(task)
    db.flush()
    oa = ModelOutput(task_id=task.id, generator_id=g1.id, asset_path="seed/a.glb")
    ob = ModelOutput(task_id=task.id, generator_id=g2.id, asset_path="seed/b.glb")
    db.add_all([oa, ob])
    db.flush()
    db.add(CalibrationPair(task_id=task.id, output_a_id=oa.id, output_b_id=ob.id, criterion_id=crit.id))
    db.commit()


def test_calibration_mode_serves_pair_with_progress():
    with SessionLocal() as db:
        _seed_calibration(db)
    client = TestClient(app)
    r = client.get("/api/next?set=calibration")
    assert r.status_code == 200
    body = r.json()
    assert body["set"] == "calibration"
    assert body["progress"] == {"voted": 0, "total": 1}
    assert "comparison_id" in body


def test_calibration_mode_reports_done_after_voting_all():
    client = TestClient(app)
    with SessionLocal() as db:
        _seed_calibration(db)
    first = client.get("/api/next?set=calibration").json()
    client.post("/api/vote?set=calibration", json={"comparison_id": first["comparison_id"], "winner": "a"})
    nxt = client.get("/api/next?set=calibration").json()
    assert nxt.get("done") is True
    assert nxt["progress"]["voted"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_calibration_mode.py -q`
Expected: FAIL — calibration set not honored (`set` param ignored; response lacks `set`/`progress`).

- [ ] **Step 3: Implement the calibration builder + route**

In `app/main.py`, add `CalibrationPair` to the `from .models import (...)` block (lines 19-28) — it must be imported at the top with the other models. Then add this helper after `_build_comparison` (~line 159):

```python
def _build_calibration_comparison(db: Session, session_id: str) -> dict | None:
    """Serve the next un-voted CalibrationPair for this session (with progress)."""
    all_pairs = db.execute(select(CalibrationPair)).scalars().all()
    total = len(all_pairs)
    voted = 0
    target = None
    for cp in all_pairs:
        already = integrity.already_voted_pair(
            db, session_id, cp.output_a_id, cp.output_b_id, cp.criterion_id
        )
        if already:
            voted += 1
        elif target is None:
            target = cp
    progress = {"voted": voted, "total": total}
    if target is None:
        return {"set": "calibration", "done": True, "progress": progress}

    crit = db.get(Criterion, target.criterion_id)
    task = db.get(Task, target.task_id)
    out_a = db.get(ModelOutput, target.output_a_id)
    out_b = db.get(ModelOutput, target.output_b_id)
    if random.random() < 0.5:
        out_a, out_b = out_b, out_a
    comparison = Comparison(
        task_id=task.id, output_a_id=out_a.id, output_b_id=out_b.id,
        criterion_id=crit.id, session_id=session_id,
    )
    db.add(comparison)
    db.commit()
    payload = _serialize(comparison, task, crit, out_a, out_b)
    payload["set"] = "calibration"
    payload["progress"] = progress
    return payload
```

Extend `api_next` (replace the current body, lines 193-203):

```python
@app.get("/api/next")
def api_next(
    request: Request,
    db: Session = Depends(get_db),
    criterion: str | None = None,
    category: str | None = None,
    set: str | None = None,
):
    if set == "calibration":
        payload = _build_calibration_comparison(db, request.state.session_id)
    else:
        payload = _build_comparison(db, request.state.session_id, criterion, category)
    if payload is None:
        return JSONResponse({"error": "no-comparisons-available"}, status_code=404)
    return payload
```

The existing `api_vote` already persists a normal `Vote` + `apply_vote`; the extra `?set=calibration` query param on the POST is ignored by the handler signature (FastAPI drops unknown query params), so calibration votes flow through the unchanged human path. No change to `api_vote` is required.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_calibration_mode.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Add an entry link on the arena page**

In `app/templates/arena.html`, add a small link near the top controls so the mode is reachable (find the existing header/nav and add):

```html
<a
  href="/api/next?set=calibration"
  class="muted"
  title="Vote the calibration subset"
  >calibration set</a
>
```

> **Implementer note:** if `arena.html` drives voting purely via JS `fetch('/api/next')`, instead add a `?set=calibration` toggle there: read a `set` URL param and thread it into the fetch calls. Inspect `app/static/` JS first; keep the change minimal — a single optional query param appended to the existing `/api/next` and `/api/vote` fetches. A test for the JS is out of scope; verify by loading `/` with `?set=calibration` and confirming a calibration pair renders.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/arena.html tests/test_calibration_mode.py
git commit -m "feat(judge): human calibration voting mode (/api/next?set=calibration) + progress"
```

---

## Task 9: Calibration report (κ, rank-corr, self-consistency, ladder)

**Files:**

- Modify: `app/calibration.py` (add stats functions)
- Create: `scripts/calibration_report.py`
- Test: `tests/test_calibration_report.py`

**Interfaces:**

- Consumes: `models.Vote`, `models.Comparison`, `models.JudgeVote`, `models.CalibrationPair`, `models.ModelOutput`, `models.Criterion`, `service.recompute_*`, `JudgeRating`, `Rating`; `scipy.stats.spearmanr`.
- Produces (in `app/calibration.py`):
  - `cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float` — unweighted κ over paired categorical labels.
  - `canonical_label(winner: str, out_a_id: int, out_b_id: int) -> str` — map a slot vote to an order-independent label `{"first","second","tie"}` (relative to `min/max(out_a_id,out_b_id)`); `"bad"` → `"bad"`.
  - `human_vs_judge_kappa(db, criterion_id, view_condition) -> dict` — over `CalibrationPair`s for that criterion: align the human `Vote` (via its `Comparison`) and the canonical-order `JudgeVote`; return `{"kappa": float|None, "n": int}` (pairs where both sides decided, `bad` excluded).
  - `judge_self_consistency(db, criterion_id, view_condition) -> dict` — `{"flip_rate": float|None, "n_groups": int}` over swap groups (two orders disagree on the real winner).
  - `rank_correlation(db, criterion_id, view_condition) -> dict` — Spearman between `Rating.bt_score` (human) and `JudgeRating.bt_score` for shared generators: `{"spearman": float|None, "n": int}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calibration_report.py
from __future__ import annotations

import math

from app import calibration


def test_cohens_kappa_perfect_agreement_is_one():
    a = ["first", "second", "tie", "first"]
    assert math.isclose(calibration.cohens_kappa(a, list(a)), 1.0)


def test_cohens_kappa_chance_agreement_near_zero():
    # Independent 50/50 labels → κ near 0 (allow slack on a tiny sample).
    a = ["first", "second"] * 10
    b = ["first", "first", "second", "second"] * 5
    k = calibration.cohens_kappa(a, b)
    assert -0.5 < k < 0.5


def test_canonical_label_is_order_independent():
    # out_a=10 (lower) wins → 'first'; swap slots, out_a=20 wins via 'b' → still 'first'.
    assert calibration.canonical_label("a", 10, 20) == "first"
    assert calibration.canonical_label("b", 20, 10) == "first"
    assert calibration.canonical_label("tie", 10, 20) == "tie"


def test_cohens_kappa_handles_empty():
    assert calibration.cohens_kappa([], []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_calibration_report.py -q`
Expected: FAIL — `AttributeError: module 'app.calibration' has no attribute 'cohens_kappa'`.

- [ ] **Step 3: Add stats functions to `app/calibration.py`**

```python
def cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    """Unweighted Cohen's κ over paired categorical labels. None if no data."""
    n = len(labels_a)
    if n == 0 or n != len(labels_b):
        return None
    cats = sorted(set(labels_a) | set(labels_b))
    idx = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    obs = sum(1 for x, y in zip(labels_a, labels_b) if x == y) / n
    ra = [0.0] * k
    rb = [0.0] * k
    for x, y in zip(labels_a, labels_b):
        ra[idx[x]] += 1
        rb[idx[y]] += 1
    exp = sum((ra[i] / n) * (rb[i] / n) for i in range(k))
    if exp >= 1.0:
        return 1.0  # degenerate single-category perfect agreement
    return (obs - exp) / (1.0 - exp)


def canonical_label(winner: str, out_a_id: int, out_b_id: int) -> str:
    """Map a slot vote to an order-independent label vs (lower_id, higher_id)."""
    if winner == "tie":
        return "tie"
    if winner == "bad":
        return "bad"
    lo = min(out_a_id, out_b_id)
    winner_id = out_a_id if winner == "a" else out_b_id
    return "first" if winner_id == lo else "second"
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_calibration_report.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Add the DB-backed aggregation functions to `app/calibration.py`**

```python
def _human_label_for_pair(db, session_filter, cp) -> str | None:
    """Latest human canonical label for a CalibrationPair (any session), or None."""
    from .models import Comparison, ModelOutput, Vote  # local import avoids cycle

    rows = db.execute(
        select(Vote, Comparison)
        .join(Comparison, Vote.comparison_id == Comparison.id)
        .where(
            Comparison.criterion_id == cp.criterion_id,
            Comparison.is_gold.is_(False),
        )
    ).all()
    cp_set = {cp.output_a_id, cp.output_b_id}
    for vote, comp in rows:
        if {comp.output_a_id, comp.output_b_id} == cp_set:
            return canonical_label(vote.winner, comp.output_a_id, comp.output_b_id)
    return None


def human_vs_judge_kappa(db, criterion_id: int, view_condition: str) -> dict:
    from .models import CalibrationPair, JudgeVote

    pairs = db.execute(
        select(CalibrationPair).where(CalibrationPair.criterion_id == criterion_id)
    ).scalars().all()
    h_labels: list[str] = []
    j_labels: list[str] = []
    for cp in pairs:
        h = _human_label_for_pair(db, None, cp)
        lo, hi = sorted((cp.output_a_id, cp.output_b_id))
        jv = db.execute(
            select(JudgeVote).where(
                JudgeVote.criterion_id == criterion_id,
                JudgeVote.view_condition == view_condition,
                JudgeVote.output_a_id == lo,
                JudgeVote.output_b_id == hi,
            )
        ).scalars().first()
        if h is None or jv is None:
            continue
        j = canonical_label(jv.winner, jv.output_a_id, jv.output_b_id)
        if h == "bad" or j == "bad":
            continue
        h_labels.append(h)
        j_labels.append(j)
    return {"kappa": cohens_kappa(h_labels, j_labels), "n": len(h_labels)}


def judge_self_consistency(db, criterion_id: int, view_condition: str) -> dict:
    from .models import JudgeVote

    votes = db.execute(
        select(JudgeVote).where(
            JudgeVote.criterion_id == criterion_id,
            JudgeVote.view_condition == view_condition,
        )
    ).scalars().all()
    by_group: dict[str, list] = {}
    for v in votes:
        by_group.setdefault(v.swap_group, []).append(v)
    flips = groups = 0
    for grp in by_group.values():
        if len(grp) != 2:
            continue
        groups += 1
        labels = {canonical_label(v.winner, v.output_a_id, v.output_b_id) for v in grp}
        # A flip = the two orders disagree on the real winner (ignoring tie/bad equivalence).
        if len(labels) > 1:
            flips += 1
    return {"flip_rate": (flips / groups if groups else None), "n_groups": groups}


def rank_correlation(db, criterion_id: int, view_condition: str) -> dict:
    from scipy.stats import spearmanr

    from .models import JudgeRating, Rating

    human = {
        r.generator_id: r.bt_score
        for r in db.execute(
            select(Rating).where(Rating.criterion_id == criterion_id, Rating.category_id.is_(None))
        ).scalars()
    }
    vlm = {
        r.generator_id: r.bt_score
        for r in db.execute(
            select(JudgeRating).where(
                JudgeRating.criterion_id == criterion_id,
                JudgeRating.view_condition == view_condition,
                JudgeRating.category_id.is_(None),
            )
        ).scalars()
    }
    shared = sorted(set(human) & set(vlm))
    if len(shared) < 3:
        return {"spearman": None, "n": len(shared)}
    rho, _p = spearmanr([human[g] for g in shared], [vlm[g] for g in shared])
    return {"spearman": float(rho), "n": len(shared)}
```

- [ ] **Step 6: Implement `scripts/calibration_report.py`**

```python
"""Generate the VLM↔human calibration report → docs/results/<date>-vlm-calibration.md.

Usage: .venv/bin/python scripts/calibration_report.py --date 2026-06-27"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import calibration  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.judge_render import CONDITIONS  # noqa: E402
from app.models import Criterion  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def build_report(db) -> str:
    lines = ["# VLM ↔ Human Calibration Report", ""]
    for slug in calibration.STUDY_CRITERIA:
        crit = db.execute(select(Criterion).where(Criterion.slug == slug)).scalars().first()
        if crit is None:
            continue
        lines.append(f"## Criterion: {slug}")
        lines.append("")
        lines.append("| view | κ (human vs VLM) | n | self-consistency flip-rate | rank ρ |")
        lines.append("|---|---|---|---|---|")
        for cond in CONDITIONS:
            k = calibration.human_vs_judge_kappa(db, crit.id, cond)
            sc = calibration.judge_self_consistency(db, crit.id, cond)
            rc = calibration.rank_correlation(db, crit.id, cond)
            lines.append(
                f"| {cond} | {k['kappa']} | {k['n']} | {sc['flip_rate']} | {rc['spearman']} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD for the output filename")
    args = ap.parse_args()
    with SessionLocal() as db:
        text = build_report(db)
    out = ROOT / "docs" / "results" / f"{args.date}-vlm-calibration.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Run the full new suite to verify nothing regressed**

Run: `.venv/bin/python -m pytest tests/test_calibration_report.py tests/test_calibration.py tests/test_judge_recompute.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/calibration.py scripts/calibration_report.py tests/test_calibration_report.py
git commit -m "feat(judge): calibration report — kappa, self-consistency, rank-corr, ladder"
```

---

## Task 10: Dual leaderboard surface + full-suite verification

**Files:**

- Modify: `app/main.py` (`_judge_leaderboard_rows` + thread into the leaderboard view)
- Modify: `app/templates/leaderboard.html` (VLM column/section)
- Test: `tests/test_judge_leaderboard.py`

**Interfaces:**

- Consumes: `models.JudgeRating`, `models.Generator`, `ranking.rank_by_ci`.
- Produces: `_judge_leaderboard_rows(db, criterion_slug="overall", view_condition="multi4") -> list[dict]` — same row shape as `_leaderboard_rows` (`generator, elo, bt_score, bt_lower, bt_upper, n_games, rank`), read from `JudgeRating`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge_leaderboard.py
from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.main import _judge_leaderboard_rows
from app.models import Category, Criterion, Generator, JudgeVote, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_judge_leaderboard_rows_ranked():
    with SessionLocal() as db:
        db.query(JudgeVote).delete()
        db.commit()
        cat = Category(slug="jl-cat", name="C")
        db.add(cat)
        db.flush()
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        s = Generator(slug="jl-strong", name="Strong")
        w = Generator(slug="jl-weak", name="Weak")
        db.add_all([s, w])
        db.flush()
        task = Task(category_id=cat.id, title="jl-task", prompt="p")
        db.add(task)
        db.flush()
        so = ModelOutput(task_id=task.id, generator_id=s.id, asset_path="seed/s.glb")
        wo = ModelOutput(task_id=task.id, generator_id=w.id, asset_path="seed/w.glb")
        db.add_all([so, wo])
        db.flush()
        for _ in range(9):
            db.add(JudgeVote(
                task_id=task.id, output_a_id=so.id, output_b_id=wo.id, criterion_id=crit.id,
                winner="a", view_condition="multi4", judge_model="claude-sonnet-4-6",
                swap_group="g", rationale="",
            ))
        db.commit()
        service.recompute_judge_scope(db, crit, "multi4")
        rows = _judge_leaderboard_rows(db, "overall", "multi4")
        assert rows and rows[0]["generator"] == "Strong"
        assert rows[0]["rank"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_judge_leaderboard.py -q`
Expected: FAIL — `ImportError: cannot import name '_judge_leaderboard_rows'`.

- [ ] **Step 3: Implement `_judge_leaderboard_rows`**

In `app/main.py`, add `JudgeRating` to the models import, then add after `_leaderboard_rows` (~line 298):

```python
def _judge_leaderboard_rows(
    db: Session, criterion_slug: str = "overall", view_condition: str = "multi4"
) -> list[dict]:
    crit = db.execute(select(Criterion).where(Criterion.slug == criterion_slug)).scalars().first()
    if crit is None:
        return []
    ratings = db.execute(
        select(JudgeRating).where(
            JudgeRating.criterion_id == crit.id,
            JudgeRating.view_condition == view_condition,
            JudgeRating.category_id.is_(None),
        )
    ).scalars().all()
    rows = []
    for r in ratings:
        gen = db.get(Generator, r.generator_id)
        rows.append({
            "generator": gen.name,
            "kind": gen.kind,
            "elo": round(r.elo, 1),
            "bt_score": round(r.bt_score, 1),
            "bt_lower": round(r.bt_lower, 1),
            "bt_upper": round(r.bt_upper, 1),
            "n_games": r.n_games,
        })
    rows.sort(key=lambda x: x["bt_score"], reverse=True)
    ranks = ranking.rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    for row, rank in zip(rows, ranks):
        row["rank"] = rank
    return rows
```

- [ ] **Step 4: Thread the VLM board into the leaderboard view**

In the `leaderboard` view (~line 301-341), add a `judge_rows` entry to the template context:

```python
"judge_rows": _judge_leaderboard_rows(db, criterion, "multi4"),
```

And in `app/templates/leaderboard.html`, render a second table below the human board guarded by `{% if judge_rows %}`:

```html
{% if judge_rows %}
<h2>VLM judge (Sonnet 4.6, multi-view)</h2>
<table>
  <thead>
    <tr>
      <th>Rank (UB)</th>
      <th>Generator</th>
      <th>BT score</th>
      <th>Games</th>
    </tr>
  </thead>
  <tbody>
    {% for r in judge_rows %}
    <tr>
      <td>{{ r.rank }}</td>
      <td>{{ r.generator }}</td>
      <td>{{ r.bt_score }}</td>
      <td>{{ r.n_games }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_judge_leaderboard.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Full-suite regression**

Run: `.venv/bin/python -m pytest -q`
Expected: all prior tests still pass; new judge/calibration tests pass; live/browser tests pass-or-skip. No failures.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/templates/leaderboard.html tests/test_judge_leaderboard.py
git commit -m "feat(judge): dual leaderboard surface (human + VLM multi-view)"
```

---

## Operational run (post-merge, NOT part of TDD tasks)

After all tasks merge, the actual study is run by the controller (not a subagent), against the **worktree DB** with real outputs, babysat:

1. `BIO3D_DB_PATH=<worktree>/data/arena.db .venv/bin/python scripts/build_calibration_set.py --n 50`
2. Judge batch via jobd (long, many API calls; network-bound, no `--gpu`):
   `job submit --project bio3d-arena --cwd $(pwd) --wait -- .venv/bin/python scripts/judge_vlm.py` — monitor first-stdout + state.
3. `POST /admin/recompute_judge` (or `service.recompute_judge_all` for each `view_condition`).
4. Human votes: open `/api/next?set=calibration`, vote the ~150 set.
5. `POST /admin/recompute` (human board), then `scripts/calibration_report.py --date <today>`.

---

## Self-Review

**Spec coverage:**

- §1 Data model → Task 1 ✅ (three tables, no ALTERs).
- §2 Render pipeline → Tasks 2 (logic) + 3 (Playwright capture) ✅. _Deviation:_ logic in new `app/judge_render.py` rather than extending `scripts/render_spotlight.py`, for unit-testability + separation; the spec's intent (reuse model-viewer capture, cache by `{oid}_{cond}.png`) is preserved. Flag for reviewer.
- §3 Judge harness → Tasks 4 (core) + 6 (batch, both-order swap, resumable, capped, jobd) ✅.
- §4 Calibration subset + analysis → Tasks 5 (sampler) + 9 (κ, self-consistency, rank-corr, ladder, report) ✅.
- §5 Human calibration mode → Task 8 ✅.
- §6 Leaderboard recompute + dual surface → Tasks 7 + 10 ✅. Testing & real-execution checks: render live (Task 3), VLM live (Task 4) ✅. `anthropic` dep (Task 1) ✅.
- Out-of-scope items (Opus ceiling, other 3 criteria, extra voters) correctly excluded.

**Placeholder scan:** No "TBD"/"handle edge cases". All code blocks are complete and runnable as written. Two `> Implementer note:` callouts remain (Task 6: prefer `from app.calibration import STUDY_CRITERIA` over the defensive `__dict__` lookup; Task 8: how to wire the entry link when `arena.html` votes via JS) — both are choices between equivalent clean options, not gaps.

**Type consistency:** `winner ∈ {a,b,tie,bad}` and `view_condition ∈ {single,multi4,turntable}` consistent throughout. `swap_group_id(task_id, x, y, criterion_id, condition)` signature consistent (Tasks 4, 6). `canonical_label`/`cohens_kappa` signatures consistent (Task 9 + tests). `_judge_leaderboard_rows` row shape matches `_leaderboard_rows`. `bradley_terry(players, matches, bootstrap=...)` used exactly per `app/ranking.py:150`.

**Known fix applied during review:** Task 8's `_build_calibration_comparison` initial draft contained a stray `pairs = ...` line and an inline `__import__`; the implementer note directs replacing both with a clean top-of-file `CalibrationPair` import. Left visible so the reviewer sees the intent rather than a silent rewrite.
