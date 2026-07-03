# Cross-Paradigm Fidelity Board — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship a read-only `/fidelity` board that ranks paradigms per taxon on biological GT fidelity (completeness primary + geometry/trait as labeled context), plus `/api/fidelity.json`.

**Architecture:** A pure aggregation function (`app/fidelity.py`) joins three existing per-output signals (completeness, metric, trait_score) into a per-taxon × paradigm scorecard; two additive routes in `app/main.py` expose it as JSON + an HTML page. No writes to any fidelity source table; no edits to RusticDune's D-Complete/D-Gen surfaces.

**Tech Stack:** FastAPI + SQLAlchemy + Jinja2 (`app/templates/`), pytest + FastAPI TestClient.

## Global Constraints

- **Read-only** on `completeness`, `metric`, `trait_score` — the board computes/writes nothing to them.
- **Do NOT edit** `app/completeness.py`, `app/dgen.py`, `scripts/run_dgen.py` (RusticDune's surfaces). Consume completeness via `app.service.completeness_rows(db)`.
- Templates live in **`app/templates/`**; render via `templates.TemplateResponse(request, "name.html", {ctx})`. Routes are **additive** in `app/main.py`, pattern `@app.get("/path", response_class=HTMLResponse)` with `db: Session = Depends(get_db)`.
- Paradigm display names via `app.paradigms.DISPLAY_NAMES.get(p, p)`; generator display names via `app.service.generator_display_names(db)`.
- **Ranking:** within each taxon, paradigms ranked by completeness `pct_complete` desc, tie-break mean `score` desc. `capture_scan` is a **reference** row (not ranked). Axes are all higher=better as exposed (geometry uses `fscore`, not raw chamfer).
- **Return-shape contract** (Task 1 produces, Tasks 2/3 consume) — exact:

```python
{
  "axes_meta": [
    {"key": "completeness", "label": "Completeness", "badge": "validated (binary κ=0.64)", "primary": True},
    {"key": "geometry",     "label": "Geometry (F-score)", "badge": "geometric proxy — weak (SP4)", "primary": False},
    {"key": "trait",        "label": "Trait fidelity", "badge": "experimental (κ-negative)", "primary": False},
  ],
  "taxa": [
    {"taxon": "Zea mays",
     "rows": [  # ranked; competitors only (paradigm != capture_scan)
        {"paradigm": "image_recon", "paradigm_label": "Image recon", "n": 8,
         "completeness": {"pct_complete": 0.75, "mean_score": 0.82, "n": 8},
         "geometry":     {"mean_fscore": 0.41, "n": 8},   # None-valued dict fields when n==0
         "trait":        {"mean_accuracy": 0.60, "n": 5},
         "best_model":   {"name": "Hunyuan3D-3.1", "score": 0.95}},
     ],
     "reference": [ {..same row shape.., "paradigm": "capture_scan"} ]},
  ],
}
```

Empty state: no completeness rows → `{"axes_meta": [...], "taxa": []}`.

---

### Task 1: Aggregation core — `app/fidelity.py::fidelity_scorecard`

**Files:**

- Create: `app/fidelity.py`
- Test: `tests/test_fidelity.py`

**Interfaces:**

- Consumes: `app.service.completeness_rows(db)` → `[{output_id, taxon, generator_id, category, score}]`; `app.models.{Metric, TraitScore, ModelOutput, Generator}`; `app.service.generator_display_names(db)`; `app.paradigms.DISPLAY_NAMES`.
- Produces: `fidelity_scorecard(db) -> dict` per the Global-Constraints contract.

- [ ] **Step 1: Write the failing test** (`tests/test_fidelity.py`)

```python
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task, Category, TraitRubric, Metric, TraitScore, Completeness
from app.fidelity import fidelity_scorecard


def _seed(db):
    cat = Category(slug="recon", name="Recon"); db.add(cat); db.flush()
    task = Task(category_id=cat.id, title="Zea mays", prompt="p", active=True); db.add(task); db.flush()
    db.add(TraitRubric(task_id=task.id, taxon="Zea mays")); db.flush()
    gens = {}
    for slug, para in [("recon-a","image_recon"), ("recon-b","image_recon"), ("lpy","procedural_expert"), ("scan","capture_scan")]:
        g = Generator(slug=slug, name=slug, kind="model", paradigm=para); db.add(g); db.flush(); gens[slug] = g
    def mo(gid, oid):
        o = ModelOutput(id=oid, task_id=task.id, generator_id=gid, title="o", asset_path="a.glb",
                        asset_format="glb", source="x", is_gold=False); db.add(o); return o
    mo(gens["recon-a"].id, 1); mo(gens["recon-a"].id, 2); mo(gens["recon-b"].id, 3)
    mo(gens["lpy"].id, 4); mo(gens["scan"].id, 5)
    db.flush()
    # completeness: recon-a strong, recon-b weak, lpy medium, scan reference
    for oid, cat_, sc in [(1,"complete",1.0),(2,"complete",0.9),(3,"fragment",0.2),(4,"partial-organism",0.6),(5,"complete",1.0)]:
        db.add(Completeness(output_id=oid, category=cat_, score=sc))
    # geometry only for some; trait only for one
    db.add(Metric(output_id=1, fscore=0.5, chamfer=0.1, status="ok"))
    db.add(TraitScore(output_id=1, botanical_accuracy=0.7, n_scored=5, n_total=5))
    db.commit()
    return task


def test_scorecard_ranks_by_completeness_and_separates_reference():
    init_db()
    with SessionLocal() as db:
        _seed(db)
        sc = fidelity_scorecard(db)
        maize = next(t for t in sc["taxa"] if t["taxon"] == "Zea mays")
        paras = [r["paradigm"] for r in maize["rows"]]
        assert "capture_scan" not in paras                      # reference, not ranked
        assert [r["paradigm"] for r in maize["reference"]] == ["capture_scan"]
        # image_recon: 2 outputs complete of 3 total? recon-a(1,2 complete), recon-b(3 fragment) -> 2/3 complete
        ir = next(r for r in maize["rows"] if r["paradigm"] == "image_recon")
        assert ir["n"] == 3 and abs(ir["completeness"]["pct_complete"] - 2/3) < 1e-6
        # procedural_expert has higher pct_complete? lpy partial -> 0/1 complete -> 0.0; image_recon 0.667 -> ranked first
        assert maize["rows"][0]["paradigm"] == "image_recon"
        # sparse axes: geometry n=1 for image_recon (only oid1), trait n=1
        assert ir["geometry"]["n"] == 1 and ir["trait"]["n"] == 1
        # best model = recon-a (score 1.0)
        assert ir["best_model"]["name"] in ("recon-a",) and ir["best_model"]["score"] == 1.0


def test_empty_state_when_no_completeness():
    init_db()
    with SessionLocal() as db:
        sc = fidelity_scorecard(db)
        assert sc["taxa"] == [] and any(a["primary"] for a in sc["axes_meta"])
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_fidelity.py -v` → FAIL (`No module named app.fidelity`).

- [ ] **Step 3: Implement `app/fidelity.py`**

```python
"""Cross-paradigm biological-fidelity scorecard (read-only aggregation).

Joins three per-output signals — completeness (primary, validated), geometry F-score
(secondary, SP4-weak proxy), trait botanical-accuracy (experimental) — into a per-taxon
x paradigm scorecard. Writes nothing. capture_scan is a GT reference, not a competitor."""

from __future__ import annotations

from collections import defaultdict

from app import paradigms, service
from app.models import Generator, Metric, ModelOutput, TraitScore

AXES_META = [
    {"key": "completeness", "label": "Completeness", "badge": "validated (binary κ=0.64)", "primary": True},
    {"key": "geometry", "label": "Geometry (F-score)", "badge": "geometric proxy — weak (SP4)", "primary": False},
    {"key": "trait", "label": "Trait fidelity", "badge": "experimental (κ-negative)", "primary": False},
]


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def fidelity_scorecard(db) -> dict:
    comp = service.completeness_rows(db)  # [{output_id, taxon, generator_id, category, score}]
    if not comp:
        return {"axes_meta": AXES_META, "taxa": []}
    para = {g.id: g.paradigm for g in db.query(Generator).all()}
    names = service.generator_display_names(db)
    fscore = {m.output_id: m.fscore for m in db.query(Metric).all()}
    trait = {t.output_id: t.botanical_accuracy for t in db.query(TraitScore).all()}

    # group completeness rows by (taxon, paradigm)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in comp:
        if r["taxon"] is None or r["generator_id"] is None:
            continue
        p = para.get(r["generator_id"])
        if p is None:
            continue
        groups[(r["taxon"], p)].append(r)

    by_taxon: dict[str, dict] = defaultdict(lambda: {"rows": [], "reference": []})
    for (taxon, p), rows in groups.items():
        oids = [r["output_id"] for r in rows]
        scores = [r["score"] for r in rows]
        n_complete = sum(1 for r in rows if r["category"] == "complete")
        geom = [fscore[o] for o in oids if o in fscore]
        tr = [trait[o] for o in oids if o in trait]
        best = max(rows, key=lambda r: (r["score"] if r["score"] is not None else -1.0))
        row = {
            "paradigm": p,
            "paradigm_label": paradigms.DISPLAY_NAMES.get(p, p),
            "n": len(rows),
            "completeness": {"pct_complete": n_complete / len(rows), "mean_score": _mean(scores), "n": len(rows)},
            "geometry": {"mean_fscore": _mean(geom), "n": len(geom)},
            "trait": {"mean_accuracy": _mean(tr), "n": len(tr)},
            "best_model": {"name": names.get(best["generator_id"], str(best["generator_id"])), "score": best["score"]},
        }
        bucket = "reference" if p == "capture_scan" else "rows"
        by_taxon[taxon][bucket].append(row)

    taxa = []
    for taxon in sorted(by_taxon):
        block = by_taxon[taxon]
        block["rows"].sort(key=lambda r: (r["completeness"]["pct_complete"], r["completeness"]["mean_score"] or 0.0), reverse=True)
        taxa.append({"taxon": taxon, "rows": block["rows"], "reference": block["reference"]})
    return {"axes_meta": AXES_META, "taxa": taxa}
```

- [ ] **Step 4: Run tests** — `pytest tests/test_fidelity.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add app/fidelity.py tests/test_fidelity.py && git commit -m "feat(fidelity): cross-paradigm scorecard aggregation"`

---

### Task 2: `/api/fidelity.json` route

**Files:**

- Modify: `app/main.py` (additive route only)
- Test: `tests/test_fidelity.py` (append)

**Interfaces:** Consumes `app.fidelity.fidelity_scorecard`. Produces `GET /api/fidelity.json` returning the dict.

- [ ] **Step 1: Write the failing test** (append)

```python
from fastapi.testclient import TestClient
from app.main import app


def test_api_fidelity_json_shape():
    init_db()
    with SessionLocal() as db:
        _seed(db)
    r = TestClient(app).get("/api/fidelity.json")
    assert r.status_code == 200
    data = r.json()
    assert [a["key"] for a in data["axes_meta"]] == ["completeness", "geometry", "trait"]
    assert any(t["taxon"] == "Zea mays" for t in data["taxa"])
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_fidelity.py::test_api_fidelity_json_shape -v` → FAIL (404).

- [ ] **Step 3: Add the route to `app/main.py`** (near the other `/api/*.json` routes; add `from app import fidelity` to the existing app imports in the same edit)

```python
@app.get("/api/fidelity.json")
def api_fidelity(db: Session = Depends(get_db)):
    return fidelity.fidelity_scorecard(db)
```

- [ ] **Step 4: Run tests** — `pytest tests/test_fidelity.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(fidelity): /api/fidelity.json route"`

---

### Task 3: `/fidelity` page + template + nav

**Files:**

- Modify: `app/main.py` (additive route), `app/templates/base.html` (one nav link)
- Create: `app/templates/fidelity.html`
- Test: `tests/test_fidelity.py` (append)

**Interfaces:** Consumes `fidelity.fidelity_scorecard`. Produces `GET /fidelity` (HTML).

- [ ] **Step 1: Write the failing test** (append)

```python
def test_fidelity_page_renders_and_empty_state():
    init_db()  # no data
    c = TestClient(app)
    r = c.get("/fidelity")
    assert r.status_code == 200
    assert "no completeness data" in r.text.lower() or "fidelity" in r.text.lower()
    with SessionLocal() as db:
        _seed(db)
    r2 = c.get("/fidelity")
    assert r2.status_code == 200
    assert "Zea mays" in r2.text
    assert "validated" in r2.text.lower() and "experimental" in r2.text.lower()  # axis badges
```

- [ ] **Step 2: Run to verify it fails** — → FAIL (404).

- [ ] **Step 3a: Add the route to `app/main.py`**

```python
@app.get("/fidelity", response_class=HTMLResponse)
def fidelity_board(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "fidelity.html", {"board": fidelity.fidelity_scorecard(db)})
```

- [ ] **Step 3b: Create `app/templates/fidelity.html`** (extend base; render per-taxon tables, axis badges, reference row, empty-state)

```html
{% extends "base.html" %} {% block content %}
<h1>Cross-Paradigm Fidelity</h1>
<p class="muted">
  Absolute biological ground-truth fidelity per taxon &mdash; the objective
  complement to the preference arena.
  <strong>No single blended score:</strong> completeness is the validated
  ranker; geometry and trait are labeled context.
</p>
<div class="axis-legend">
  {% for a in board.axes_meta %}<span class="badge"
    >{{ a.label }} &mdash; {{ a.badge }}</span
  >{% endfor %}
</div>
{% if not board.taxa %}
<p class="empty">
  No completeness data yet &mdash; run
  <code>scripts/score_completeness.py</code>.
</p>
{% endif %} {% for t in board.taxa %}
<h2>{{ t.taxon }}</h2>
<table class="board">
  <thead>
    <tr>
      <th>#</th>
      <th>Paradigm</th>
      <th>n</th>
      <th>Completeness (%complete / mean)</th>
      <th>Geometry (F-score)</th>
      <th>Trait acc.</th>
      <th>Best model</th>
    </tr>
  </thead>
  <tbody>
    {% for r in t.rows %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ r.paradigm_label }}</td>
      <td>{{ r.n }}</td>
      <td>
        {{ (100*r.completeness.pct_complete)|round(0)|int }}% / {{
        r.completeness.mean_score|round(2) }}
      </td>
      <td>
        {% if r.geometry.n %}{{ r.geometry.mean_fscore|round(2) }}
        <span class="muted">(n={{ r.geometry.n }})</span>{% else %}&mdash;{%
        endif %}
      </td>
      <td>
        {% if r.trait.n %}{{ r.trait.mean_accuracy|round(2) }}
        <span class="muted">(n={{ r.trait.n }})</span>{% else %}&mdash;{% endif
        %}
      </td>
      <td>{{ r.best_model.name }}</td>
    </tr>
    {% endfor %} {% for r in t.reference %}
    <tr class="reference">
      <td>ref</td>
      <td>{{ r.paradigm_label }} <span class="muted">(GT)</span></td>
      <td>{{ r.n }}</td>
      <td>
        {{ (100*r.completeness.pct_complete)|round(0)|int }}% / {{
        r.completeness.mean_score|round(2) }}
      </td>
      <td colspan="3" class="muted">
        reference upper-bound &mdash; not ranked
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endfor %} {% endblock %}
```

- [ ] **Step 3c: Add nav link** in `app/templates/base.html` after the `/procedural` link (line ~35):

```html
<a href="/fidelity">Fidelity</a>
```

- [ ] **Step 4: Run tests** — `pytest tests/test_fidelity.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(fidelity): /fidelity board page + nav"`

---

## Self-review notes

- Spec path correction: templates are in **`app/templates/`** (spec said `templates/`).
- `Metric.fscore` / `TraitScore.botanical_accuracy` / `Completeness.{category,score}` / `service.completeness_rows` / `service.generator_display_names` / `paradigms.DISPLAY_NAMES` all verified against live source 2026-07-02.
- Tests seed their own in-memory-ish DB via `init_db()`; never touch the study DB.
