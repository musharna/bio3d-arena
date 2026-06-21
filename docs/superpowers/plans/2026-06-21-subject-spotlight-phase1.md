# Subject Spotlight — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the curated, internal Subject Spotlight page — per-subject render-grid of every model we have, with all metrics, deterministic failure flags, and click-to-open live 3D viewers — on our existing recon data.

**Architecture:** A static curated `SPOTLIGHTS` list (code constant, no DB table) drives `/spotlight` + `/spotlight/<slug>`. Each page resolves its subject `Task`, lists that Task's non-gold `ModelOutput`s, joins each to its `Metric` (→ deterministic `derive_flags`) and a new per-output `Critique` row (thumbnail render + future critic note), and renders a grid. A Playwright batch (`scripts/render_spotlight.py`) drives the existing `<model-viewer>` to capture PNG thumbnails. Two additive schema changes: provenance columns on `ModelOutput`, and the `Critique` table.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (SQLite), Jinja2, vanilla JS, Google `<model-viewer>` (already CDN-loaded), Playwright (already installed), pytest.

## Global Constraints

- Python 3.13; SQLAlchemy 2.0 `Mapped`/`mapped_column` style — match `app/models.py`.
- New ORM columns are **additive and nullable** (or defaulted); the live SQLite DB is migrated with `ALTER TABLE ADD COLUMN` (Task 6). Fresh DBs get them via `Base.metadata.create_all`.
- Spotlight is **internal**: linked from `/admin`, NOT added to the public nav in `app/templates/base.html`.
- Provenance default for our own assets: `source == "bio3d-arena"`, `external_url is None` (null ⇒ hosted locally).
- Batch jobs commit **per output** (never hold the SQLite write lock across a render) — same discipline as `recon_service.rescore_all`.
- `_utcnow` is the existing timestamp helper in `app/models.py`; reuse it.
- Run tests with `.venv/bin/python -m pytest`; lint with `ruff check app/ tests/`.

---

### Task 1: Provenance columns on ModelOutput

**Files:**

- Modify: `app/models.py` (the `ModelOutput` class)
- Test: `tests/test_provenance.py`

**Interfaces:**

- Produces: `ModelOutput.source: str` (default `"bio3d-arena"`), `ModelOutput.license: str | None`, `ModelOutput.attribution: str | None`, `ModelOutput.external_url: str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provenance.py
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_model_output_provenance_defaults():
    db = SessionLocal()
    try:
        cat = Category(slug="c-prov", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-prov", prompt="p")
        gen = Generator(slug="g-prov", name="G")
        db.add_all([task, gen])
        db.flush()
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.flush()
        assert out.source == "bio3d-arena"
        assert out.license is None
        assert out.attribution is None
        assert out.external_url is None
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_provenance.py -v`
Expected: FAIL — `AttributeError: 'ModelOutput' object has no attribute 'source'`.

- [ ] **Step 3: Add the columns**

In `app/models.py`, inside `class ModelOutput`, after the `created` column add:

```python
    # Provenance (readies externally-sourced models). For our own assets:
    # source="bio3d-arena", external_url=None (null ⇒ hosted locally).
    source: Mapped[str] = mapped_column(String(64), default="bio3d-arena")
    license: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attribution: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_provenance.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_provenance.py
git commit -m "feat(spotlight): provenance columns on ModelOutput"
```

---

### Task 2: Critique table

**Files:**

- Modify: `app/models.py` (new `Critique` class, after `Metric`)
- Test: `tests/test_critique_model.py`

**Interfaces:**

- Produces: `Critique(output_id: int [unique FK], render_path: str|None, critic_note: str="", dists: float|None, dreamsim: float|None, status: str="ok", computed: datetime)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_critique_model.py
from app.database import SessionLocal, init_db
from app.models import Category, Critique, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_critique_round_trips():
    db = SessionLocal()
    try:
        cat = Category(slug="c-crit", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="t-crit", prompt="p")
        gen = Generator(slug="g-crit", name="G")
        db.add_all([task, gen])
        db.flush()
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out)
        db.flush()
        c = Critique(output_id=out.id, render_path="renders/x.png", critic_note="flat petals")
        db.add(c)
        db.flush()
        got = db.query(Critique).filter_by(output_id=out.id).one()
        assert got.render_path == "renders/x.png"
        assert got.critic_note == "flat petals"
        assert got.status == "ok"
        assert got.dists is None
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_critique_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'Critique'`.

- [ ] **Step 3: Add the model**

In `app/models.py`, after the `Metric` class add:

```python
class Critique(Base):
    """Per-output Spotlight render + qualitative/perceptual critique. One row per
    ModelOutput (upsert by output_id), best-effort. render_path is the captured
    thumbnail; critic_note/dists/dreamsim are populated in Phase 2."""

    __tablename__ = "critique"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), unique=True, index=True)
    render_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    critic_note: Mapped[str] = mapped_column(Text, default="")
    dists: Mapped[float | None] = mapped_column(Float, nullable=True)
    dreamsim: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error
    computed: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_critique_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_critique_model.py
git commit -m "feat(spotlight): Critique table (per-output render + note)"
```

---

### Task 3: Deterministic failure-flag deriver

**Files:**

- Create: `app/spotlight.py`
- Test: `tests/test_spotlight_flags.py`

**Interfaces:**

- Consumes: `app.models.Metric` (fields: `chamfer`, `gt_band_lo`, `gt_band_hi`, `coverage`, `fscore`, `status`).
- Produces: `derive_flags(metric: Metric | None) -> list[tuple[str, str]]` — each `(kind, label)` where `kind ∈ {"ok","shape","coverage","surface","unscored"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spotlight_flags.py
from app.models import Metric
from app.spotlight import derive_flags


def _m(**kw):
    m = Metric(output_id=1, status=kw.pop("status", "ok"))
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_unscored_when_no_metric_or_error():
    assert derive_flags(None) == [("unscored", "no objective score")]
    assert derive_flags(_m(status="error")) == [("unscored", "no objective score")]


def test_within_band_is_ok():
    flags = derive_flags(_m(chamfer=0.12, gt_band_lo=0.10, gt_band_hi=0.14, coverage=0.8, fscore=0.8))
    assert ("ok", "within natural variation") in flags


def test_chamfer_above_band_is_shape_fail():
    flags = derive_flags(_m(chamfer=0.18, gt_band_lo=0.10, gt_band_hi=0.14, coverage=0.8, fscore=0.8))
    assert ("shape", "outside natural variation") in flags
    assert ("ok", "within natural variation") not in flags


def test_low_coverage_and_low_fscore_flagged():
    flags = derive_flags(_m(chamfer=0.12, gt_band_lo=0.10, gt_band_hi=0.14, coverage=0.41, fscore=0.40))
    kinds = {k for k, _ in flags}
    assert "coverage" in kinds
    assert "surface" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_spotlight_flags.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.spotlight'`.

- [ ] **Step 3: Implement `derive_flags`**

```python
# app/spotlight.py
"""Subject Spotlight: deterministic failure-flag derivation + page-data assembly.

A Spotlight is a curated deep-dive on one benchmark subject, showing every model we
have for it with all metrics, failure flags, and (Phase 2) critic notes. Internal
inspection tool — see docs/superpowers/specs/2026-06-21-subject-spotlight-design.md.
"""

from __future__ import annotations

from .models import Metric

# Tunable thresholds (initial; see spec §Components).
COVERAGE_MIN = 0.5
FSCORE_MIN = 0.5


def derive_flags(metric: Metric | None) -> list[tuple[str, str]]:
    """Deterministic failure/ok flags from a Metric. Each flag is (kind, label);
    kind drives a CSS severity class. Never raises."""
    if metric is None or metric.status != "ok" or metric.chamfer is None:
        return [("unscored", "no objective score")]
    flags: list[tuple[str, str]] = []
    lo, hi, ch = metric.gt_band_lo, metric.gt_band_hi, metric.chamfer
    if hi is not None and ch > hi:
        flags.append(("shape", "outside natural variation"))
    elif lo is not None and hi is not None and lo <= ch <= hi:
        flags.append(("ok", "within natural variation"))
    if metric.coverage is not None and metric.coverage < COVERAGE_MIN:
        flags.append(("coverage", "missing geometry"))
    if metric.fscore is not None and metric.fscore < FSCORE_MIN:
        flags.append(("surface", "low F-score@τ"))
    return flags or [("ok", "scored")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_spotlight_flags.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/spotlight.py tests/test_spotlight_flags.py
git commit -m "feat(spotlight): deterministic metric→failure-flag deriver"
```

---

### Task 4: Curation list, page-data builder, routes, templates

**Files:**

- Modify: `app/spotlight.py` (add `SPOTLIGHTS`, `find_spotlight`, `build_spotlight`)
- Modify: `app/main.py` (add `/spotlight` + `/spotlight/<slug>` routes; near the `/tasks` route)
- Modify: `app/templates/admin.html` (add an internal link to `/spotlight`)
- Create: `app/templates/spotlight_index.html`, `app/templates/spotlight.html`
- Test: `tests/test_spotlight_page.py`

**Interfaces:**

- Consumes: `derive_flags` (Task 3); `app.models.{Task, ModelOutput, Metric, Critique, Generator}`; `app.storage.get_storage().url_for`.
- Produces:
  - `SPOTLIGHTS: list[dict]` — keys `slug, task_title, featured, order, blurb, reference_image`.
  - `find_spotlight(slug: str) -> dict | None`.
  - `build_spotlight(db, slug: str) -> dict | None` — returns `{slug, title, blurb, featured, reference_image, models: [ {generator, format, asset_url, thumbnail_url, metrics: {...}, flags: [...], critic_note, provenance: {...}} ]}` or `None` if the subject Task is absent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spotlight_page.py
from fastapi.testclient import TestClient

from app import ingest, spotlight
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Category, Generator, Metric, Task


def setup_module(_m):
    init_db()


def _seed_subject(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(slug="plants", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="Spotlight Test Subject", prompt="p")
    db.add(task)
    db.flush()
    for gslug, ch in [("m-a", 0.12), ("m-b", 0.18)]:
        out, _ = ingest.register_output(
            db, task_id=task.id, generator_slug=gslug, data=b"glTF-stub", ext="glb",
            title=f"out {gslug}",
        )
        db.add(Metric(output_id=out.id, status="ok", chamfer=ch, gt_band_lo=0.10, gt_band_hi=0.14,
                      coverage=0.8, fscore=0.8))
    db.commit()
    return task


def test_build_spotlight_assembles_models(monkeypatch):
    db = SessionLocal()
    try:
        _seed_subject(db)
        monkeypatch.setattr(spotlight, "SPOTLIGHTS", [
            {"slug": "test", "task_title": "Spotlight Test Subject", "featured": True,
             "order": 0, "blurb": "b", "reference_image": None},
        ])
        data = spotlight.build_spotlight(db, "test")
        assert data is not None
        assert len(data["models"]) == 2
        # the 0.18 model must carry a shape flag; the 0.12 model must be ok
        flags = {m["generator"]: [k for k, _ in m["flags"]] for m in data["models"]}
        assert "shape" in flags["m-b"]
        assert "ok" in flags["m-a"]
        assert data["models"][0]["provenance"]["source"] == "bio3d-arena"
    finally:
        db.close()


def test_spotlight_route_renders(monkeypatch):
    db = SessionLocal()
    try:
        _seed_subject(db)
    finally:
        db.close()
    monkeypatch.setattr(spotlight, "SPOTLIGHTS", [
        {"slug": "test", "task_title": "Spotlight Test Subject", "featured": True,
         "order": 0, "blurb": "b", "reference_image": None},
    ])
    client = TestClient(app)
    assert client.get("/spotlight").status_code == 200
    page = client.get("/spotlight/test")
    assert page.status_code == 200
    assert "m-a" in page.text and "m-b" in page.text
    assert client.get("/spotlight/does-not-exist").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_spotlight_page.py -v`
Expected: FAIL — `AttributeError: module 'app.spotlight' has no attribute 'SPOTLIGHTS'`.

- [ ] **Step 3: Add curation + builder to `app/spotlight.py`**

Append to `app/spotlight.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Critique, Generator, Metric, ModelOutput, Task
from .storage import get_storage

# Curated subjects (internal, hand-picked). reference_image is an optional public
# real-photo path under the asset store; None ⇒ no reference image (Phase 1).
SPOTLIGHTS: list[dict] = [
    {
        "slug": "tomato",
        "task_title": "Solanum lycopersicum — single-image → 3D reconstruction",
        "featured": True,
        "order": 0,
        "blurb": "How current image→3D models handle a whole tomato plant.",
        "reference_image": None,
    },
    {
        "slug": "arabidopsis",
        "task_title": "Arabidopsis thaliana — single-image → 3D reconstruction",
        "featured": False,
        "order": 1,
        "blurb": "Thale cress rosette — fine structure stress test.",
        "reference_image": None,
    },
]


def find_spotlight(slug: str) -> dict | None:
    return next((s for s in SPOTLIGHTS if s["slug"] == slug), None)


def _metrics_dict(m: Metric | None) -> dict:
    if m is None:
        return {}
    return {
        "chamfer": m.chamfer, "fscore": m.fscore, "coverage": m.coverage,
        "tau": m.tau, "gt_band_lo": m.gt_band_lo, "gt_band_hi": m.gt_band_hi,
        "within_variation": m.species_verdict,
    }


def build_spotlight(db: Session, slug: str) -> dict | None:
    spot = find_spotlight(slug)
    if spot is None:
        return None
    task = db.execute(select(Task).where(Task.title == spot["task_title"])).scalars().first()
    if task is None:
        return None
    storage = get_storage()
    outs = db.execute(
        select(ModelOutput).where(ModelOutput.task_id == task.id, ModelOutput.is_gold.is_(False))
    ).scalars().all()
    models = []
    for o in outs:
        metric = db.execute(select(Metric).where(Metric.output_id == o.id)).scalars().first()
        crit = db.execute(select(Critique).where(Critique.output_id == o.id)).scalars().first()
        gen = db.get(Generator, o.generator_id)
        models.append({
            "generator": gen.slug if gen else "?",
            "generator_name": gen.name if gen else "?",
            "format": o.asset_format,
            "asset_url": storage.url_for(o.asset_path),
            "thumbnail_url": storage.url_for(crit.render_path) if crit and crit.render_path else None,
            "metrics": _metrics_dict(metric),
            "flags": derive_flags(metric),
            "critic_note": crit.critic_note if crit else "",
            "provenance": {
                "source": o.source, "license": o.license,
                "attribution": o.attribution, "external_url": o.external_url,
            },
        })
    return {
        "slug": spot["slug"], "title": spot["task_title"], "blurb": spot["blurb"],
        "featured": spot["featured"], "reference_image": spot["reference_image"],
        "models": models,
    }
```

- [ ] **Step 4: Add routes to `app/main.py`**

After the `/tasks` route (`tasks_page`), add:

```python
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
```

- [ ] **Step 5: Create `app/templates/spotlight_index.html`**

```html
{% extends "base.html" %} {% block title %}Spotlight · Bio 3D Arena{% endblock
%} {% block content %}
<section class="board">
  <h2>Subject Spotlight <span class="subtle">(internal)</span></h2>
  <p class="subtle">
    Per-subject deep dive: every model side-by-side, all metrics, failure flags.
  </p>
  <ul class="spotlight-list">
    {% for s in subjects %}
    <li>
      <a href="/spotlight/{{ s.slug }}">{{ s.task_title }}</a> {% if s.featured
      %}<span class="cat-chip">featured</span>{% endif %}
      <div class="subtle">{{ s.blurb }}</div>
    </li>
    {% endfor %}
  </ul>
</section>
{% endblock %}
```

- [ ] **Step 6: Create `app/templates/spotlight.html`**

```html
{% extends "base.html" %} {% block title %}{{ s.title }} · Spotlight{% endblock
%} {% block content %}
<section class="board">
  <h2>{{ s.title }}</h2>
  <p class="subtle">{{ s.blurb }}</p>
  <div class="reference-panel">
    {% if s.reference_image %}<img
      src="{{ s.reference_image }}"
      alt="reference"
      class="ref-img"
    />
    {% else %}
    <div class="subtle">No reference image curated.</div>
    {% endif %}
  </div>
  <div class="spotlight-grid">
    {% for m in s.models %}
    <div class="spotlight-card">
      <div
        class="thumb"
        data-asset="{{ m.asset_url }}"
        data-format="{{ m.format }}"
      >
        {% if m.thumbnail_url %}<img
          src="{{ m.thumbnail_url }}"
          alt="{{ m.generator }}"
        />
        {% else %}
        <div class="thumb-placeholder">▶ click to view</div>
        {% endif %}
      </div>
      <div class="card-gen">{{ m.generator_name }}</div>
      <table class="metric-table">
        <tr>
          <td>chamfer</td>
          <td>
            {{ "%.3f"|format(m.metrics.chamfer) if m.metrics.chamfer is not none
            else "—" }}
          </td>
        </tr>
        <tr>
          <td>F@τ</td>
          <td>
            {{ "%.2f"|format(m.metrics.fscore) if m.metrics.fscore is not none
            else "—" }}
          </td>
        </tr>
        <tr>
          <td>coverage</td>
          <td>
            {{ "%.2f"|format(m.metrics.coverage) if m.metrics.coverage is not
            none else "—" }}
          </td>
        </tr>
        <tr>
          <td>GT band</td>
          <td>
            {% if m.metrics.gt_band_lo is not none %}{{
            "%.2f"|format(m.metrics.gt_band_lo) }}–{{
            "%.2f"|format(m.metrics.gt_band_hi) }}{% else %}—{% endif %}
          </td>
        </tr>
      </table>
      <div class="flags">
        {% for kind, label in m.flags %}<span class="flag flag-{{ kind }}"
          >{{ label }}</span
        >{% endfor %}
      </div>
      {% if m.critic_note %}
      <div class="critic-note">{{ m.critic_note }}</div>
      {% endif %}
      <div class="provenance subtle">
        {{ m.provenance.source }}{% if m.provenance.license %} · {{
        m.provenance.license }}{% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
  <div id="live-viewer-slot"></div>
</section>
<script type="module" src="/static/spotlight.js"></script>
{% endblock %}
```

- [ ] **Step 7: Create `app/static/spotlight.js` (click-to-live)**

```javascript
// Click a thumbnail → load that GLB into a single live <model-viewer>.
document.querySelectorAll(".thumb").forEach((t) => {
  t.addEventListener("click", () => {
    const url = t.getAttribute("data-asset");
    const slot = document.getElementById("live-viewer-slot");
    slot.innerHTML = "";
    const mv = document.createElement("model-viewer");
    mv.setAttribute("src", url);
    mv.setAttribute("camera-controls", "");
    mv.setAttribute("auto-rotate", "");
    mv.style.width = "100%";
    mv.style.height = "480px";
    slot.appendChild(mv);
    slot.scrollIntoView({ behavior: "smooth" });
  });
});
```

- [ ] **Step 8: Link from `app/templates/admin.html`**

After the "Recompute Leaderboard" form's closing `</form>`, add:

```html
<div class="card">
  <h3>Subject Spotlight</h3>
  <p class="subtle">Internal per-subject model deep-dive.</p>
  <a href="/spotlight">Open Spotlight →</a>
</div>
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_spotlight_page.py -v`
Expected: PASS (both tests).

- [ ] **Step 10: Real-execution page check (Playwright screenshot, no console errors)**

Create `scripts/shoot_spotlight.py` mirroring the existing screenshot scripts (transient uvicorn on a random port against a seeded DB, Playwright load `/spotlight/<slug>`, assert N `.spotlight-card` elements and zero console errors, save a PNG to the job tmp). Run it and confirm the grid renders.

- [ ] **Step 11: Commit**

```bash
git add app/spotlight.py app/main.py app/templates/spotlight_index.html app/templates/spotlight.html app/static/spotlight.js app/templates/admin.html tests/test_spotlight_page.py scripts/shoot_spotlight.py
git commit -m "feat(spotlight): curated /spotlight pages — grid, metrics, flags, click-to-live"
```

---

### Task 5: Thumbnail render pipeline

**Files:**

- Create: `scripts/render_spotlight.py`
- Test: `tests/test_render_spotlight.py`

**Interfaces:**

- Consumes: `app.models.{ModelOutput, Critique, Task}`, `app.config.ASSET_DIR`, Playwright, `<model-viewer>`.
- Produces: per output, a PNG under `ASSET_DIR/renders/<output_id>.png`; sets `Critique.render_path = "renders/<output_id>.png"` (upsert by output_id), committing per output.
- Exposes `render_outputs(db, output_ids: list[int], *, capture) -> dict` where `capture(glb_abs_path) -> bytes` is injectable so the unit test runs without a browser.

- [ ] **Step 1: Write the failing test (injected capture, no browser)**

```python
# tests/test_render_spotlight.py
from app.database import SessionLocal, init_db
from app.models import Category, Critique, Generator, ModelOutput, Task
from scripts.render_spotlight import render_outputs


def setup_module(_m):
    init_db()


def test_render_outputs_upserts_critique(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    db = SessionLocal()
    try:
        cat = Category(slug="c-r", name="C"); db.add(cat); db.flush()
        task = Task(category_id=cat.id, title="t-r", prompt="p")
        gen = Generator(slug="g-r", name="G"); db.add_all([task, gen]); db.flush()
        (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
        (tmp_path / "seed" / "x.glb").write_bytes(b"glTF-stub")
        out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb")
        db.add(out); db.commit()
        res = render_outputs(db, [out.id], capture=lambda p: b"\x89PNG-fake-bytes")
        assert res["rendered"] == 1
        crit = db.query(Critique).filter_by(output_id=out.id).one()
        assert crit.render_path == f"renders/{out.id}.png"
        assert (tmp_path / crit.render_path).read_bytes() == b"\x89PNG-fake-bytes"
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_render_spotlight.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.render_spotlight'`.

- [ ] **Step 3: Implement the pipeline**

```python
# scripts/render_spotlight.py
"""Capture a static <model-viewer> PNG thumbnail per ModelOutput → Critique.render_path.

A transient http.server roots ASSET_DIR so model-viewer fetches the GLB over http
(file:// is blocked by browser security). Playwright drives model-viewer, waits for
its load event, screenshots the element. Commits per output (never holds the SQLite
write lock across a render — see recon_service.rescore_all)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app import config  # noqa: E402
from app.models import Critique, ModelOutput  # noqa: E402


def _get_or_create_critique(db, output_id: int) -> Critique:
    c = db.execute(select(Critique).where(Critique.output_id == output_id)).scalars().first()
    if c is None:
        c = Critique(output_id=output_id)
        db.add(c)
        db.flush()
    return c


def render_outputs(db, output_ids: list[int], *, capture) -> dict:
    """capture(glb_abs_path: str) -> png bytes. Injectable for tests."""
    rendered = errors = 0
    renders_dir = Path(config.ASSET_DIR) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    for oid in output_ids:
        out = db.get(ModelOutput, oid)
        c = _get_or_create_critique(db, oid)
        try:
            glb_abs = str(Path(config.ASSET_DIR) / out.asset_path)
            png = capture(glb_abs)
            rel = f"renders/{oid}.png"
            (Path(config.ASSET_DIR) / rel).write_bytes(png)
            c.render_path = rel
            c.status = "ok"
            rendered += 1
        except Exception as e:  # noqa: BLE001 — best-effort batch
            c.status = "error"
            c.critic_note = f"render failed: {e}"
            errors += 1
        db.commit()  # per-output: release the write lock between renders
    return {"rendered": rendered, "errors": errors}


def _browser_capture_factory():
    """Real capture: a transient static server + Playwright model-viewer screenshot.
    Returns capture(glb_abs_path) -> png bytes. Heavy import, so built lazily."""
    import http.server
    import socketserver
    import threading

    asset_root = str(config.ASSET_DIR)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=asset_root, **k)

        def log_message(self, *a):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch()

    def capture(glb_abs_path: str) -> bytes:
        rel = str(Path(glb_abs_path).relative_to(config.ASSET_DIR)).replace("\\", "/")
        html = (
            '<script type="module" '
            'src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js">'
            "</script>"
            f'<model-viewer id="mv" src="http://127.0.0.1:{port}/{rel}" '
            'camera-orbit="30deg 75deg auto" environment-image="neutral" '
            'style="width:512px;height:512px;background:#fff"></model-viewer>'
        )
        page = browser.new_page(viewport={"width": 512, "height": 512})
        page.set_content(html)
        page.wait_for_function("document.querySelector('#mv')?.loaded === true", timeout=30000)
        png = page.locator("#mv").screenshot()
        page.close()
        return png

    return capture


def main() -> int:
    import argparse

    from app.database import SessionLocal
    from app.models import Task
    from app.spotlight import find_spotlight

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="spotlight slug to render")
    args = ap.parse_args()
    db = SessionLocal()
    spot = find_spotlight(args.slug)
    if spot is None:
        print(f"no spotlight '{args.slug}'")
        return 1
    task = db.execute(select(Task).where(Task.title == spot["task_title"])).scalars().first()
    if task is None:
        print("subject task not present")
        return 1
    oids = [
        o.id
        for o in db.execute(
            select(ModelOutput).where(ModelOutput.task_id == task.id, ModelOutput.is_gold.is_(False))
        ).scalars()
    ]
    res = render_outputs(db, oids, capture=_browser_capture_factory())
    print(res)
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_render_spotlight.py -v`
Expected: PASS.

- [ ] **Step 5: Real-execution check (actual browser render of a real GLB)**

With the live audit DB present and a real recon GLB on disk, run:
`.venv/bin/python scripts/render_spotlight.py --slug tomato`
Confirm it prints `{'rendered': N, 'errors': 0}` and that `data/assets/renders/<id>.png` files exist and are non-empty (`file` reports PNG). This is the real-execution boundary check (per the real-execution doctrine — the injected-capture unit test is paired with one real browser render).

- [ ] **Step 6: Commit**

```bash
git add scripts/render_spotlight.py tests/test_render_spotlight.py
git commit -m "feat(spotlight): Playwright/model-viewer thumbnail render pipeline"
```

---

### Task 6: Migrate the live audit DB + verify end-to-end

**Files:** none (operational).

**Interfaces:** Consumes everything above.

- [ ] **Step 1: Add the new columns/table to the live DB**

`Base.metadata.create_all` creates the `critique` table but does NOT alter `model_output`. Add the provenance columns to the live SQLite DB:

```bash
cd /home/mjarnold/bio3d-arena/.claude/worktrees/bio3d-arena-mvp
BIO3D_DATABASE_URL="sqlite:///data/arena.db" .venv/bin/python - <<'PY'
from app.database import engine, init_db
from sqlalchemy import text
init_db()  # creates the new `critique` table
with engine.begin() as c:
    cols = {r[1] for r in c.execute(text("PRAGMA table_info(model_output)"))}
    if "source" not in cols:
        c.execute(text("ALTER TABLE model_output ADD COLUMN source VARCHAR(64) DEFAULT 'bio3d-arena'"))
        c.execute(text("ALTER TABLE model_output ADD COLUMN license VARCHAR(128)"))
        c.execute(text("ALTER TABLE model_output ADD COLUMN attribution VARCHAR(256)"))
        c.execute(text("ALTER TABLE model_output ADD COLUMN external_url VARCHAR(512)"))
print("migrated")
PY
```

- [ ] **Step 2: Render the featured subject's thumbnails**

Run: `.venv/bin/python scripts/render_spotlight.py --slug tomato` (expect `errors: 0`).

- [ ] **Step 3: Verify the page in the running server**

Restart the dev server, then `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/spotlight/tomato` → `200`. Spot-check the page shows the grid with thumbnails + flags.

- [ ] **Step 4: Full suite + lint + independent-critic gate**

```bash
.venv/bin/python -m pytest -q
ruff check app/ tests/
```

Then run a fresh, reference-grounded independent critic on the rendered `/spotlight/tomato` page (per the independent-critic doctrine) before considering Phase 1 done.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A && git commit -m "chore(spotlight): live-DB migration + Phase 1 verification"
```

---

## Self-Review

**Spec coverage:** routes (Task 4) ✓; curation list (Task 4) ✓; provenance schema (Task 1) ✓; Critique table (Task 2) ✓; metric-flag deriver (Task 3) ✓; render pipeline (Task 5) ✓; reference panel (Task 4 template — real-photo-or-none; internal GT render deferred per spec fallback) ✓; dense metrics/flags page (Task 4) ✓; internal/admin-linked, not public nav (Task 4 step 8) ✓; per-output commit discipline (Task 5) ✓; tests incl. real-execution render (Task 5 steps 1 & 5) ✓; independent-critic gate (Task 6) ✓. Phase 2 (DISTS/DreamSim + qualitative notes) is intentionally out of this plan.

**Placeholder scan:** no "TBD"/"add error handling"/"similar to" — each step carries complete code. The `SPOTLIGHTS` blurbs are real content, not placeholders.

**Type consistency:** `derive_flags(metric) -> list[tuple[str,str]]` used consistently (Tasks 3, 4). `Critique` fields (`output_id`, `render_path`, `critic_note`, `dists`, `dreamsim`, `status`) match across Tasks 2, 4, 5. `render_outputs(db, output_ids, *, capture)` signature matches test and `main()` caller. `build_spotlight` return keys match the template (`s.models[*].{generator_name, thumbnail_url, asset_url, metrics, flags, critic_note, provenance}`).
