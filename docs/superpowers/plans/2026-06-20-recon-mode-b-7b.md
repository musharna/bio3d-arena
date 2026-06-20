# Recon Mode-B Integration — §7B bio3d-arena Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give bio3d-arena an objective recon-accuracy ("Mode B") track for 3D plant reconstructions — a `Metric` table, a batch scoring step that calls AgriGen's `POST /score` microservice, and a Mode-B leaderboard + vote↔metric agreement view — built so the AgriGen-dependent parts are cleanly stubbed.

**Architecture:** Mirror the existing Inc4 objective-validation pattern (`validation_service.py` + `/admin/revalidate` + `reference_accuracy` page). A new `Metric` table (created via `create_all`, no ALTER) stores the metric bundle + pinned confounds + GT version hash per `ModelOutput`. A new `recon_client.py` POSTs GLB bytes to AgriGen's `/score` service; `recon_service.py` orchestrates compute→store + aggregation; a `/benchmark` page renders Mode-B + agreement. The scorer is **injectable** so all tests run against a fake — the live round-trip is a deferred real-execution gate (needs §7A A2).

**Tech Stack:** FastAPI + SQLite/SQLAlchemy + Jinja2/vanilla-JS, httpx (already a dep via TestClient), pytest. numpy for the local rank-correlation.

## Global Constraints

- **Resolved cross-session decisions (D1–D6, user-approved 2026-06-20):** D1 microservice boundary (bio3d-arena calls AgriGen `POST /score`, never imports `agrigen`); D2 GT private/held server-side (no public viewer overlay); D3 confounds stored as typed columns, AgriGen sets values; D4 Mode-B first / votes fast-follow; D5 roster TRELLIS/Hunyuan3D-2/InstantMesh/SF3D + baseline + GT-LOO ceiling; D6 new typed `Metric` table reusing the `validation_service`/`/admin/revalidate`/`reference_accuracy` patterns.
- **Scoring is a BATCH/admin step**, NOT inline in `POST /api/outputs` / `ingest.register_output` (recon scoring is heavy). Mirror `POST /admin/revalidate`.
- **Test env:** `.venv/bin/python -m pytest`. Lint: `ruff check app tests` (ruff on PATH, not in `.venv`).
- **No `import eval.*` / no `agrigen` import** (microservice boundary). The agreement view uses a LOCAL rank-correlation; the richer 2AFC `eval/agreement.py` math is a later service-returned field, out of scope here.
- **Migration:** `database.py:init_db` = `Base.metadata.create_all` (idempotent, no Alembic). New tables only — do NOT add columns to existing tables (create_all won't ALTER).
- **`POST /score` contract (proposed; AgriGen confirms before live wiring):** request = multipart `glb` (bytes) + `task_id` (str) + optional `point_count`,`seed`; response JSON = `{chamfer, nearest_shape_distance, nearest_gt_idx, fscore_at_tau, tau, coverage, species_verdict ("PASS"|"FAIL"), gt_band:{lo,hi}, confounds:{point_count, icp_seed, scorer_version, gt_version_hash}}`.
- **Doctrine — real-execution gate:** the fake-scorer unit tests MUST be paired with one live `/score` round-trip once A2 ships, before B2 is called done. This plan ships the fake-tested scaffold and marks that gate open.
- ruff PostToolUse formatter can strip imports added before first use — add import + first use in the same edit, re-grep.

## Out of scope (GATED on §7A — not executable tasks here)

- Live `/score` round-trip (needs AgriGen A2 service up). The client is built + fake-tested; live wiring is a config flip + the real-execution gate.
- **B4** GT side-by-side viewer — constrained by D2 (GT is private). Deferred until a public-safe GT-render policy is set.
- **B5** seed the real plant-recon benchmark — needs AgriGen A3 (GT bundle) + A4 (bake-off outputs). This plan seeds only synthetic Metric rows for tests.

---

### Task 1 (B1): `Metric` table

**Files:**

- Modify: `app/models.py` (append a `Metric` class)
- Test: `tests/test_metric_model.py`

**Interfaces:**

- Consumes: `Base`, `ModelOutput` (existing).
- Produces: `Metric` ORM model — columns `output_id` (FK model_output, unique), `chamfer`, `nearest_shape_distance`, `nearest_gt_idx`, `fscore`, `tau`, `coverage`, `species_verdict`, `gt_band_lo`, `gt_band_hi`, `point_count`, `icp_seed`, `scorer_version`, `gt_version_hash`, `status`, `detail`, `computed`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metric_model.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Category, Generator, Metric, ModelOutput, Task


def setup_module(_module):
    init_db()


def test_metric_row_roundtrips():
    db = SessionLocal()
    try:
        cat = Category(slug="plants-test-m", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="recon-t", prompt="p")
        gen = Generator(slug="trellis-test-m", name="TRELLIS")
        db.add_all([task, gen])
        db.flush()
        out = ModelOutput(
            task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
        )
        db.add(out)
        db.flush()
        m = Metric(
            output_id=out.id,
            chamfer=0.012,
            nearest_shape_distance=0.012,
            nearest_gt_idx=2,
            fscore=0.81,
            tau=0.01,
            coverage=0.74,
            species_verdict="PASS",
            gt_band_lo=0.008,
            gt_band_hi=0.02,
            point_count=16384,
            icp_seed=0,
            scorer_version="agrigen-eval@abc123",
            gt_version_hash="sha256:deadbeef",
            status="ok",
            detail="",
        )
        db.add(m)
        db.commit()
        got = db.query(Metric).filter(Metric.output_id == out.id).one()
        assert got.chamfer == 0.012
        assert got.species_verdict == "PASS"
        assert got.gt_version_hash == "sha256:deadbeef"
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metric_model.py -q`
Expected: FAIL — `cannot import name 'Metric' from 'app.models'`.

- [ ] **Step 3: Append the `Metric` model to `app/models.py`**

Read `app/models.py` first (Iron Law). After the `Rating` class (or at end of file, before any trailing code), add:

```python
class Metric(Base):
    """Objective recon-accuracy score for one ModelOutput vs a task's GT cloud set.

    Mode-B counterpart to the vote-driven Rating. Populated by the batch scorer
    (recon_service) from AgriGen's /score microservice. One row per output (latest);
    rescoring overwrites. Confounds + versions are typed columns (fairness §6.2/§6.1).
    """

    __tablename__ = "metric"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(
        ForeignKey("model_output.id"), unique=True, index=True
    )
    # Holistic measures (§6.4) — never rank on chamfer alone.
    chamfer: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_shape_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_gt_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    tau: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    species_verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)  # PASS|FAIL
    gt_band_lo: Mapped[float | None] = mapped_column(Float, nullable=True)
    gt_band_hi: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Pinned confounds + reproducibility (§6.1/§6.2).
    point_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    icp_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scorer_version: Mapped[str] = mapped_column(String(128), default="")
    gt_version_hash: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|skipped
    detail: Mapped[str] = mapped_column(Text, default="")
    computed: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

Confirm `Float`, `Integer`, `String`, `Text`, `DateTime`, `ForeignKey`, `_utcnow` are already imported in `models.py` (they are, per the existing models). No new imports needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metric_model.py -q`
Expected: PASS (1 passed). If the DB file predates the table, `init_db()`'s `create_all` adds it idempotently.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_metric_model.py
git commit -m "feat(recon): Metric table for Mode-B objective recon-accuracy scores"
```

---

### Task 2 (B2-scaffold): recon scorer client + batch store step

**Files:**

- Modify: `app/config.py` (add `RECON_SCORER_URL`)
- Create: `app/recon_client.py`
- Create: `app/recon_service.py`
- Modify: `app/main.py` (`POST /admin/rescore`)
- Test: `tests/test_recon_service.py`

**Interfaces:**

- Consumes: `Metric`, `ModelOutput` (Task 1); `get_storage()`; `RECON_SCORER_URL`.
- Produces:
  - `recon_client.score_output(glb_bytes: bytes, task_id: int, *, base_url: str, point_count: int = 16384, seed: int = 0, timeout: float = 120.0) -> dict` — POSTs to `{base_url}/score`, returns the §contract dict; raises `recon_client.ScorerError` on transport/HTTP failure.
  - `recon_service.score_and_store(db, output, *, scorer) -> Metric` — `scorer(glb_bytes, task_id) -> dict` (injectable; default wraps `recon_client.score_output`). Maps the contract dict → `Metric` columns, upserts by `output_id`, flush, returns the row. Best-effort: a scorer failure stores `status="error"`.
  - `recon_service.rescore_all(db, *, scorer) -> dict` — `{outputs, scored, errors, skipped}` over GLB non-gold outputs.

- [ ] **Step 1: Write the failing test (fake scorer — no live service)**

```python
# tests/test_recon_service.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Category, Generator, Metric, ModelOutput, Task
from app import recon_service


def setup_module(_module):
    init_db()


FAKE_CARD = {
    "chamfer": 0.013,
    "nearest_shape_distance": 0.013,
    "nearest_gt_idx": 1,
    "fscore_at_tau": 0.79,
    "tau": 0.01,
    "coverage": 0.71,
    "species_verdict": "PASS",
    "gt_band": {"lo": 0.009, "hi": 0.021},
    "confounds": {
        "point_count": 16384,
        "icp_seed": 0,
        "scorer_version": "fake@1",
        "gt_version_hash": "sha256:cafe",
    },
}


def _mk_output(db, slug):
    cat = Category(slug=f"c-{slug}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"t-{slug}", prompt="p")
    gen = Generator(slug=f"g-{slug}", name="M")
    db.add_all([task, gen])
    db.flush()
    out = ModelOutput(
        task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
    )
    db.add(out)
    db.flush()
    return out


def test_score_and_store_maps_contract_to_metric():
    db = SessionLocal()
    try:
        out = _mk_output(db, "ok")
        m = recon_service.score_and_store(db, out, scorer=lambda b, t: FAKE_CARD)
        assert m.status == "ok"
        assert m.chamfer == 0.013
        assert m.fscore == 0.79
        assert m.point_count == 16384
        assert m.gt_version_hash == "sha256:cafe"
        assert m.gt_band_lo == 0.009 and m.gt_band_hi == 0.021
    finally:
        db.close()


def test_scorer_failure_records_error_not_crash():
    db = SessionLocal()
    try:
        out = _mk_output(db, "err")

        def boom(b, t):
            raise RuntimeError("scorer down")

        m = recon_service.score_and_store(db, out, scorer=boom)
        assert m.status == "error"
        assert "scorer down" in m.detail
    finally:
        db.close()


def test_rescore_all_skips_non_glb():
    db = SessionLocal()
    try:
        out = _mk_output(db, "pdb")
        out.asset_format = "pdb"
        db.flush()
        detail = recon_service.rescore_all(db, scorer=lambda b, t: FAKE_CARD)
        assert detail["skipped"] >= 1
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_recon_service.py -q`
Expected: FAIL — `No module named 'app.recon_service'`.

- [ ] **Step 3: Add `RECON_SCORER_URL` to `app/config.py`**

Read `app/config.py` first. Add alongside the other env-driven settings (match the existing `os.getenv` pattern):

```python
RECON_SCORER_URL = os.getenv("RECON_SCORER_URL", "http://127.0.0.1:8800")
```

- [ ] **Step 4: Create `app/recon_client.py`**

```python
"""Client for AgriGen's recon-scoring microservice (POST /score).

Microservice boundary (decision D1): bio3d-arena never imports agrigen's heavy 3D
deps — it ships GLB bytes over HTTP and gets back the metric bundle. The base URL is
config.RECON_SCORER_URL.
"""

from __future__ import annotations

import httpx


class ScorerError(Exception):
    """Raised when the recon-scoring service is unreachable or returns non-2xx."""


def score_output(
    glb_bytes: bytes,
    task_id: int,
    *,
    base_url: str,
    point_count: int = 16384,
    seed: int = 0,
    timeout: float = 120.0,
) -> dict:
    """POST GLB bytes + task_id to {base_url}/score; return the metric-bundle dict."""
    files = {"glb": ("output.glb", glb_bytes, "model/gltf-binary")}
    data = {"task_id": str(task_id), "point_count": str(point_count), "seed": str(seed)}
    try:
        resp = httpx.post(f"{base_url}/score", files=files, data=data, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise ScorerError(f"recon scorer at {base_url}: {e}") from e
```

- [ ] **Step 5: Create `app/recon_service.py`**

```python
"""DB wiring for Mode-B recon scoring: call the scorer, map the contract bundle into a
Metric row (upsert by output_id), best-effort. Mirrors validation_service / the
/admin/revalidate batch shape.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import Metric, ModelOutput
from .recon_client import score_output
from .storage import get_storage

MESH_FORMATS = {"glb", "gltf"}


def _default_scorer(glb_bytes: bytes, task_id: int) -> dict:
    return score_output(glb_bytes, task_id, base_url=config.RECON_SCORER_URL)


def _get_or_create_metric(db: Session, output_id: int) -> Metric:
    m = db.execute(select(Metric).where(Metric.output_id == output_id)).scalars().first()
    if m is None:
        m = Metric(output_id=output_id)
        db.add(m)
        db.flush()
    return m


def score_and_store(db: Session, output: ModelOutput, *, scorer=_default_scorer) -> Metric:
    """Score one output's GLB vs its task GT and upsert a Metric row (best-effort)."""
    m = _get_or_create_metric(db, output.id)
    try:
        glb = get_storage().read(output.asset_path)
        card = scorer(glb, output.task_id)
        conf = card.get("confounds", {})
        band = card.get("gt_band", {}) or {}
        m.chamfer = card.get("chamfer")
        m.nearest_shape_distance = card.get("nearest_shape_distance")
        m.nearest_gt_idx = card.get("nearest_gt_idx")
        m.fscore = card.get("fscore_at_tau")
        m.tau = card.get("tau")
        m.coverage = card.get("coverage")
        m.species_verdict = card.get("species_verdict")
        m.gt_band_lo = band.get("lo")
        m.gt_band_hi = band.get("hi")
        m.point_count = conf.get("point_count")
        m.icp_seed = conf.get("icp_seed")
        m.scorer_version = conf.get("scorer_version", "")
        m.gt_version_hash = conf.get("gt_version_hash", "")
        m.status = "ok"
        m.detail = ""
    except Exception as e:  # noqa: BLE001 — best-effort; capture and continue the batch
        m.status = "error"
        m.detail = str(e)
    db.flush()
    return m


def rescore_all(db: Session, *, scorer=_default_scorer) -> dict:
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    scored = errors = skipped = 0
    for o in outs:
        if (o.asset_format or "").lower() not in MESH_FORMATS:
            skipped += 1
            continue
        m = score_and_store(db, o, scorer=scorer)
        if m.status == "ok":
            scored += 1
        else:
            errors += 1
    db.commit()
    return {"outputs": len(outs), "scored": scored, "errors": errors, "skipped": skipped}
```

- [ ] **Step 6: Add `POST /admin/rescore` to `app/main.py`**

Read the `admin_revalidate` route first; add directly after it (same token/`_require_admin` pattern):

```python
@app.post("/admin/rescore")
def admin_rescore(token: str = Form(...), db: Session = Depends(get_db)):
    _require_admin(token)
    from . import recon_service

    detail = recon_service.rescore_all(db)
    return JSONResponse({"status": "rescored", "detail": detail})
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_recon_service.py -q`
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
git add app/config.py app/recon_client.py app/recon_service.py app/main.py tests/test_recon_service.py
git commit -m "feat(recon): scorer client + batch score-and-store step + /admin/rescore (fake-tested)

Live /score round-trip is a DEFERRED real-execution gate (needs AgriGen A2 service)."
```

---

### Task 3 (B3): Mode-B leaderboard + vote↔metric agreement view

**Files:**

- Modify: `app/recon_service.py` (add `recon_leaderboard`, `agreement`)
- Modify: `app/main.py` (`GET /benchmark`, `GET /api/benchmark`)
- Create: `app/templates/benchmark.html`
- Modify: `app/templates/base.html` (nav link "Benchmark")
- Test: `tests/test_recon_service.py` (extend)

**Interfaces:**

- Consumes: `Metric` rows (Task 2); `service`/`Rating` for Mode-A BT ranks (read existing `_leaderboard_rows` or `Rating` directly).
- Produces:
  - `recon_service.recon_leaderboard(db, task_id) -> list[dict]` — per-output `{generator, chamfer, fscore, coverage, species_verdict, gt_band}`, sorted by chamfer asc (None last); excludes gold.
  - `recon_service.agreement(db, task_id) -> dict` — `{spearman, n, rows:[{generator, metric_rank, vote_rank}]}` comparing metric rank (by chamfer asc) vs Mode-A BT rank (by bt_score desc) for the task's category scope; Spearman computed locally (no agrigen import).

- [ ] **Step 1: Write the failing test (seeded mock Metric rows)**

```python
# append to tests/test_recon_service.py
def test_recon_leaderboard_sorts_by_chamfer():
    db = SessionLocal()
    try:
        out = _mk_output(db, "lb")
        # second output on the same task
        gen2 = Generator(slug="g-lb2", name="M2")
        db.add(gen2)
        db.flush()
        out2 = ModelOutput(
            task_id=out.task_id, generator_id=gen2.id, asset_path="seed/y.glb", asset_format="glb"
        )
        db.add(out2)
        db.flush()
        recon_service.score_and_store(db, out, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.05})
        recon_service.score_and_store(db, out2, scorer=lambda b, t: {**FAKE_CARD, "chamfer": 0.01})
        db.commit()
        board = recon_service.recon_leaderboard(db, out.task_id)
        assert [r["chamfer"] for r in board] == [0.01, 0.05]  # best (lowest) first
    finally:
        db.close()


def test_agreement_returns_spearman_and_rows():
    db = SessionLocal()
    try:
        out = _mk_output(db, "agr")
        recon_service.score_and_store(db, out, scorer=lambda b, t: FAKE_CARD)
        db.commit()
        agr = recon_service.agreement(db, out.task_id)
        assert "spearman" in agr
        assert isinstance(agr["rows"], list)
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_recon_service.py -q -k "leaderboard or agreement"`
Expected: FAIL — `recon_service has no attribute 'recon_leaderboard'`.

- [ ] **Step 3: Add aggregations to `app/recon_service.py`**

```python
def _gen_name(db: Session, gid: int) -> str:
    from .models import Generator

    g = db.get(Generator, gid)
    return g.name if g else str(gid)


def recon_leaderboard(db: Session, task_id: int) -> list[dict]:
    outs = db.execute(
        select(ModelOutput).where(
            ModelOutput.task_id == task_id, ModelOutput.is_gold.is_(False)
        )
    ).scalars().all()
    rows = []
    for o in outs:
        m = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        if m is None or m.status != "ok":
            continue
        rows.append(
            {
                "generator": _gen_name(db, o.generator_id),
                "chamfer": m.chamfer,
                "fscore": m.fscore,
                "coverage": m.coverage,
                "species_verdict": m.species_verdict,
                "gt_band": [m.gt_band_lo, m.gt_band_hi],
            }
        )
    rows.sort(key=lambda r: (r["chamfer"] is None, r["chamfer"] if r["chamfer"] is not None else 0))
    return rows


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    import numpy as np

    if len(xs) < 2:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rank, i in enumerate(order):
            r[i] = rank
        return r

    rx, ry = np.array(ranks(xs)), np.array(ranks(ys))
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def agreement(db: Session, task_id: int) -> dict:
    """Compare metric rank (chamfer asc) vs Mode-A BT rank (bt_score desc) per generator."""
    from .models import Rating, Task

    task = db.get(Task, task_id)
    cat_id = task.category_id if task else None
    board = recon_leaderboard(db, task_id)
    rows = []
    metric_vals, vote_vals = [], []
    for r in board:
        from .models import Generator

        gen = db.execute(select(Generator).where(Generator.name == r["generator"])).scalars().first()
        if gen is None:
            continue
        rating = db.execute(
            select(Rating).where(
                Rating.generator_id == gen.id,
                (Rating.category_id == cat_id) | (Rating.category_id.is_(None)),
            )
        ).scalars().first()
        bt = rating.bt_score if rating else None
        rows.append({"generator": r["generator"], "chamfer": r["chamfer"], "bt_score": bt})
        if r["chamfer"] is not None and bt is not None:
            metric_vals.append(r["chamfer"])  # lower = better
            vote_vals.append(-bt)  # negate so lower = better, aligning sense with chamfer
    sp = _spearman(metric_vals, vote_vals)
    # attach ranks for display
    def rank_map(vals, key):
        ordered = sorted(rows, key=lambda x: (x[key] is None, x[key] if x[key] is not None else 0))
        return {id(r): i + 1 for i, r in enumerate(ordered)}

    mr = rank_map(rows, "chamfer")
    vr = sorted(rows, key=lambda x: (x["bt_score"] is None, -(x["bt_score"] or 0)))
    vrank = {id(r): i + 1 for i, r in enumerate(vr)}
    out_rows = [
        {"generator": r["generator"], "metric_rank": mr[id(r)], "vote_rank": vrank[id(r)]}
        for r in rows
    ]
    return {"spearman": sp, "n": len(metric_vals), "rows": out_rows}
```

- [ ] **Step 4: Add routes to `app/main.py`**

Read the `/validation` route first; add near it:

```python
@app.get("/benchmark", response_class=HTMLResponse)
def benchmark_page(request: Request, db: Session = Depends(get_db), task_id: int | None = None):
    from . import recon_service
    from .models import Task

    tasks = db.execute(select(Task)).scalars().all()
    if task_id is None and tasks:
        task_id = tasks[0].id
    board = recon_service.recon_leaderboard(db, task_id) if task_id else []
    agree = recon_service.agreement(db, task_id) if task_id else {"spearman": None, "rows": []}
    return templates.TemplateResponse(
        request,
        "benchmark.html",
        {"tasks": tasks, "task_id": task_id, "board": board, "agree": agree},
    )


@app.get("/api/benchmark")
def api_benchmark(db: Session = Depends(get_db), task_id: int | None = None):
    from . import recon_service

    if task_id is None:
        return JSONResponse({"error": "task_id required"}, status_code=400)
    return JSONResponse(
        {
            "leaderboard": recon_service.recon_leaderboard(db, task_id),
            "agreement": recon_service.agreement(db, task_id),
        }
    )
```

- [ ] **Step 5: Create `app/templates/benchmark.html`**

```html
{% extends "base.html" %} {% block title %}Benchmark · Bio 3D Arena{% endblock
%} {% block content %}
<section class="board">
  <h2>
    Recon benchmark
    <span class="subtle">— objective accuracy vs ground-truth scan</span>
  </h2>
  <p class="subtle">
    Mode-B: single-image→3D reconstruction scored against held-out GT plant
    scans (chamfer / F-score / coverage). Distinct from the Mode-A perceptual
    vote; the
    <b>agreement</b> table shows where the metric and the votes disagree.
  </p>
  <form class="filter-row" method="get" action="/benchmark">
    <label
      >Task
      <select name="task_id" onchange="this.form.submit()">
        {% for t in tasks %}
        <option
          value="{{ t.id }}"
          {%
          if
          t.id=""
          ="task_id"
          %}selected{%
          endif
          %}
        >
          {{ t.title }}
        </option>
        {% endfor %}
      </select>
    </label>
  </form>

  {% if board %}
  <table class="ranktable">
    <thead>
      <tr>
        <th>#</th>
        <th>Method</th>
        <th>Chamfer ↓</th>
        <th>F-score ↑</th>
        <th>Coverage ↑</th>
        <th>Verdict</th>
        <th>GT band</th>
      </tr>
    </thead>
    <tbody>
      {% for r in board %}
      <tr>
        <td>{{ loop.index }}</td>
        <td class="gen">{{ r.generator }}</td>
        <td class="num strong">
          {{ '%.4f'|format(r.chamfer) if r.chamfer is not none else '—' }}
        </td>
        <td class="num">
          {{ '%.3f'|format(r.fscore) if r.fscore is not none else '—' }}
        </td>
        <td class="num">
          {{ '%.3f'|format(r.coverage) if r.coverage is not none else '—' }}
        </td>
        <td>{{ r.species_verdict or '—' }}</td>
        <td class="num">
          {{ '%.3f–%.3f'|format(r.gt_band[0], r.gt_band[1]) if r.gt_band[0] is
          not none else '—' }}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <h3>
    Vote ↔ metric agreement
    <span class="subtle"
      >— Spearman {{ '%.2f'|format(agree.spearman) if agree.spearman is not none
      else 'n/a' }}</span
    >
  </h3>
  <table class="ranktable">
    <thead>
      <tr>
        <th>Method</th>
        <th>Metric rank</th>
        <th>Vote rank</th>
      </tr>
    </thead>
    <tbody>
      {% for r in agree.rows %}
      <tr>
        <td class="gen">{{ r.generator }}</td>
        <td class="num">{{ r.metric_rank }}</td>
        <td class="num">{{ r.vote_rank }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p>
    No recon scores yet for this task. Run <b>Rescore</b> in Admin once outputs
    + the GT scorer are wired.
  </p>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 6: Add the nav link in `app/templates/base.html`**

Read base.html; in the `<nav>` block after the Validation link add:

```html
<a href="/benchmark">Benchmark</a>
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_recon_service.py -q`
Expected: PASS.

- [ ] **Step 8: Full suite + ruff**

Run: `.venv/bin/python -m pytest -q && ruff check app tests`
Expected: all pass, ruff clean.

- [ ] **Step 9: Screenshot-verify `/benchmark`** (seed a couple of mock Metric rows via a throwaway DB, like `shoot_validation.py`) — confirm the Mode-B table + agreement render. Optional but recommended (Playwright already installed).

- [ ] **Step 10: Commit**

```bash
git add app/recon_service.py app/main.py app/templates/benchmark.html app/templates/base.html tests/test_recon_service.py
git commit -m "feat(recon): Mode-B benchmark leaderboard + vote↔metric agreement view"
```

---

## Final verification (controller, before merge)

- [ ] Full suite ≥2× green (Inc2 lesson): `.venv/bin/python -m pytest -q` twice.
- [ ] ruff clean.
- [ ] Independent review of `git diff master..HEAD` (Inc3/Inc4 caught real bugs) — focus: Metric upsert semantics, the contract→column mapping, the agreement rank/sign logic (lower chamfer = better vs higher bt = better), no `agrigen` import leaked in.
- [ ] Confirm the **no-leak** rule still holds: `_serialize` (arena payload) unchanged; GT never served.
- [ ] Suite-gated ff-merge to master.
- [ ] Update the AgriGen-integration memory (mark B1–B3 done, B4/B5 + live gate still open) + leave the `/score` contract + open real-execution gate noted for the AgriGen session.

## Self-review notes (author)

- **Spec coverage:** B1 (Metric table) ✓ Task 1; B2 (metric-compute step) ✓ Task 2 (scaffold, live gate deferred); B3 (Mode-B column + agreement) ✓ Task 3; B4/B5 explicitly out-of-scope/gated. Fairness §6: confound columns ✓ (Task 1), holistic measures (never chamfer alone — F-score+coverage+verdict+GT band shown) ✓ (Task 3 table), versioning (scorer_version+gt_version_hash) ✓.
- **Divergence flagged:** agreement uses a LOCAL Spearman, not `eval/agreement.py` (microservice boundary forbids the import). The richer 2AFC agreement is a later service-returned field.
- **Read-before-edit at execution (Iron Law):** `models.py` (imports/`_utcnow`), `config.py` (env pattern), `main.py` (`admin_revalidate` + `/validation` routes), `base.html` (nav) — confirm current line context before editing each.
- **Open cross-session items:** the `/score` contract (AgriGen confirm); the live real-execution gate (needs A2); B5 seed (needs A3/A4).
