# Procedural code-gen scorecard (`/procedural`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the commissioned `procedural_llm` arena as a dedicated `/procedural` scorecard page (pass@1 + experimental morphology fidelity), built entirely from existing DB data.

**Architecture:** One read-only aggregation `service.procedural_scorecard(db)` reads `CommissionAttempt` (execution outcomes) + `TraitVerdict`/`ModelScope` (morphology, gated exactly like existing scoring) per `procedural_llm` generator. Two routes render it: `GET /procedural` (HTML via a new `procedural.html`) and `GET /api/procedural.json`. A nav link is added. No new tables, no generation, no spend.

**Tech Stack:** FastAPI + Jinja2 + SQLAlchemy (SQLite), pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- Only generators with `paradigm == "procedural_llm"` appear in the scorecard.
- Attempts join to their generator via `CommissionAttempt.generator_id` (integer FK) — **never** match on the `model_id` string.
- Commissioned outputs are `ModelOutput` rows with `source == "commissioned"`, joined via `ModelOutput.generator_id`.
- Morphology assessability MUST mirror existing scoring at `app/service.py:771` exactly: `is_assessable(scopes.get(output_id), {"key": v.trait_key, "trait_class": v.trait_class})` where `scopes = service.load_scopes(db)`.
- Mesh vertex count key is `"vertices"` inside `CommissionAttempt.mesh_stats_json` (JSON string).
- Morphology fidelity is **experimental / uncalibrated** — the page copy MUST say so (the Mode-C κ-gate is open).
- Rank by `pass_at_1` desc, tiebreak `morph_fidelity` desc (None sorts last).
- Model names are shown (not anonymized). `Generator.name` holds the model_id.
- TDD: write the failing test first each task. Do not run pytest with `BIO3D_DATABASE_URL` / `BIO3D_DATA_DIR` pointed at the study DB (it wipes it — known incident); the default test config uses an isolated DB, so just run `pytest` plainly.
- Commit at the end of each task.

---

### Task 1: `service.procedural_scorecard(db)` aggregation

**Files:**

- Modify: `app/service.py` (add `CommissionAttempt` to the `from .models import (...)` block at lines 20-39; append the new function after `load_scopes` / near the trait helpers)
- Test: `tests/test_procedural_scorecard.py` (create)

**Interfaces:**

- Consumes: `Generator` (has `.id`, `.name`, `.paradigm`), `CommissionAttempt` (`.generator_id`, `.status`, `.mesh_stats_json`), `ModelOutput` (`.id`, `.generator_id`, `.source`), `TraitVerdict` (`.output_id`, `.trait_key`, `.trait_class`, `.verdict`), `service.load_scopes(db) -> dict[int, dict]`, `scope.is_assessable(scope, trait) -> bool` (already imported in service.py at line 17).
- Produces: `service.procedural_scorecard(db: Session) -> list[dict]`, each dict with keys `model` (str), `attempts` (int), `valid` (int), `pass_at_1` (float), `morph_correct` (int), `morph_assessable` (int), `morph_fidelity` (float | None), `median_verts` (int), `n` (int). Consumed by Task 2's routes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_procedural_scorecard.py`:

```python
from __future__ import annotations

import json
import uuid

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    CommissionAttempt,
    Generator,
    ModelOutput,
    ModelScope,
    Task,
    TraitVerdict,
)


def setup_module(_m):
    init_db()


def _mk_task(db) -> int:
    cat = Category(slug=f"proc-cat-{uuid.uuid4().hex}", name="C")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=f"proc-{uuid.uuid4().hex}", prompt="p")
    db.add(t)
    db.flush()
    return t.id


def test_scorecard_pass_at_1_fidelity_rank_and_exclusion():
    with SessionLocal() as db:
        tag = uuid.uuid4().hex
        # Generator A: procedural_llm, 2/2 ok attempts.
        gen_a = Generator(
            slug=f"pa-{tag}", name=f"model-a-{tag}", kind="model", paradigm="procedural_llm"
        )
        # Generator B: procedural_llm, 1/2 ok attempts.
        gen_b = Generator(
            slug=f"pb-{tag}", name=f"model-b-{tag}", kind="model", paradigm="procedural_llm"
        )
        # Generator C: different paradigm — must be excluded.
        gen_c = Generator(
            slug=f"pc-{tag}", name=f"model-c-{tag}", kind="model", paradigm="image_recon"
        )
        db.add_all([gen_a, gen_b, gen_c])
        db.flush()

        t1, t2 = _mk_task(db), _mk_task(db)
        db.add_all(
            [
                CommissionAttempt(
                    task_id=t1, model_id=gen_a.name, generator_id=gen_a.id, status="ok",
                    mesh_stats_json=json.dumps({"vertices": 100}),
                ),
                CommissionAttempt(
                    task_id=t2, model_id=gen_a.name, generator_id=gen_a.id, status="ok",
                    mesh_stats_json=json.dumps({"vertices": 300}),
                ),
                CommissionAttempt(
                    task_id=t1, model_id=gen_b.name, generator_id=gen_b.id, status="ok",
                    mesh_stats_json=json.dumps({"vertices": 50}),
                ),
                CommissionAttempt(
                    task_id=t2, model_id=gen_b.name, generator_id=gen_b.id, status="error",
                    mesh_stats_json="{}",
                ),
                CommissionAttempt(
                    task_id=t1, model_id=gen_c.name, generator_id=gen_c.id, status="ok",
                    mesh_stats_json=json.dumps({"vertices": 999}),
                ),
            ]
        )
        # A commissioned output for gen_a with a plant scope + trait verdicts.
        out = ModelOutput(
            task_id=t1, generator_id=gen_a.id, asset_path=f"commissioned/{tag}.glb",
            source="commissioned",
        )
        db.add(out)
        db.flush()
        db.add(
            ModelScope(
                output_id=out.id, is_plant=True,
                parts_json=json.dumps(["whole_plant"]), judge_model="j",
            )
        )
        # presence class => is_assessable True regardless of parts.
        db.add_all(
            [
                TraitVerdict(
                    output_id=out.id, rubric_id=0, trait_key="k1", trait_class="presence",
                    verdict="present_correct",
                ),
                TraitVerdict(
                    output_id=out.id, rubric_id=0, trait_key="k2", trait_class="presence",
                    verdict="present_wrong",
                ),
                TraitVerdict(
                    output_id=out.id, rubric_id=0, trait_key="k3", trait_class="presence",
                    verdict="not_assessable",
                ),
            ]
        )
        db.commit()

        rows = service.procedural_scorecard(db)
        by_model = {r["model"]: r for r in rows}

        assert gen_c.name not in by_model  # non-procedural_llm excluded

        a = by_model[gen_a.name]
        assert a["attempts"] == 2 and a["valid"] == 2
        assert a["pass_at_1"] == 1.0
        assert a["median_verts"] == 200  # median(100, 300)
        # 2 assessable (present_correct + present_wrong), na dropped; 1 correct.
        assert a["morph_correct"] == 1 and a["morph_assessable"] == 2
        assert a["morph_fidelity"] == 0.5
        assert a["n"] == 2

        b = by_model[gen_b.name]
        assert b["attempts"] == 2 and b["valid"] == 1
        assert b["pass_at_1"] == 0.5
        assert b["morph_fidelity"] is None  # no verdicts
        assert b["median_verts"] == 50

        # Ranked by pass_at_1 desc: A (1.0) before B (0.5).
        model_order = [r["model"] for r in rows if r["model"] in (gen_a.name, gen_b.name)]
        assert model_order == [gen_a.name, gen_b.name]


def test_scorecard_empty_when_no_procedural_generators():
    with SessionLocal() as db:
        # A generator with a non-procedural paradigm and no attempts must not appear;
        # the list may legitimately contain other tests' rows, so assert our tag is absent.
        tag = uuid.uuid4().hex
        g = Generator(slug=f"pe-{tag}", name=f"m-{tag}", kind="model", paradigm="retrieval")
        db.add(g)
        db.commit()
        rows = service.procedural_scorecard(db)
        assert all(r["model"] != g.name for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_procedural_scorecard.py -v`
Expected: FAIL — `AttributeError: module 'app.service' has no attribute 'procedural_scorecard'` (and an ImportError only if `CommissionAttempt` is not yet importable from `app.models` — it is defined there, so the test import succeeds).

- [ ] **Step 3: Add `CommissionAttempt` to the service imports**

In `app/service.py`, extend the existing `from .models import (...)` block (lines 20-39) to include `CommissionAttempt` in alphabetical position (before `Comparison`):

```python
from .models import (
    Category,
    CommissionAttempt,
    Comparison,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    Metric,
    ModelOutput,
    ModelScope,
    Rating,
    Task,
    TaskDifficulty,
    TraitCalibration,
    TraitRubric,
    TraitScore,
    TraitVerdict,
    Vote,
    VoterSession,
)
```

- [ ] **Step 4: Implement `procedural_scorecard`**

Append to `app/service.py` (after `load_scopes`, near the trait helpers). Use local `import json` / `import statistics` matching the codebase's local-import style in `load_scopes`:

```python
def procedural_scorecard(db: Session) -> list[dict]:
    """Per-model scorecard for the procedural_llm paradigm (LLMs authoring Blender-Python).
    Existing data only. pass@1 = valid/attempts from CommissionAttempt (status 'ok').
    Morphology fidelity = present_correct / scope-assessable non-na TraitVerdicts on the
    generator's commissioned outputs — EXPERIMENTAL/uncalibrated (Mode-C kappa-gate open).
    One row per procedural_llm generator, ranked by pass@1 desc (tiebreak morph_fidelity)."""
    import json
    import statistics

    gens = (
        db.execute(select(Generator).where(Generator.paradigm == "procedural_llm"))
        .scalars()
        .all()
    )
    if not gens:
        return []
    scopes = load_scopes(db)
    rows: list[dict] = []
    for gen in gens:
        attempts = (
            db.execute(select(CommissionAttempt).where(CommissionAttempt.generator_id == gen.id))
            .scalars()
            .all()
        )
        n_attempts = len(attempts)
        ok = [a for a in attempts if a.status == "ok"]
        n_valid = len(ok)
        pass_at_1 = (n_valid / n_attempts) if n_attempts else 0.0

        verts: list[int] = []
        for a in ok:
            try:
                verts.append(int(json.loads(a.mesh_stats_json or "{}").get("vertices", 0)))
            except (ValueError, TypeError):
                continue
        median_verts = int(statistics.median(verts)) if verts else 0

        out_ids = (
            db.execute(
                select(ModelOutput.id).where(
                    ModelOutput.generator_id == gen.id,
                    ModelOutput.source == "commissioned",
                )
            )
            .scalars()
            .all()
        )
        morph_correct = 0
        morph_assessable = 0
        if out_ids:
            verdicts = (
                db.execute(select(TraitVerdict).where(TraitVerdict.output_id.in_(out_ids)))
                .scalars()
                .all()
            )
            for v in verdicts:
                if v.verdict == "not_assessable":
                    continue
                if not is_assessable(
                    scopes.get(v.output_id),
                    {"key": v.trait_key, "trait_class": v.trait_class},
                ):
                    continue
                morph_assessable += 1
                if v.verdict == "present_correct":
                    morph_correct += 1
        morph_fidelity = (morph_correct / morph_assessable) if morph_assessable else None

        rows.append(
            {
                "model": gen.name,
                "attempts": n_attempts,
                "valid": n_valid,
                "pass_at_1": pass_at_1,
                "morph_correct": morph_correct,
                "morph_assessable": morph_assessable,
                "morph_fidelity": morph_fidelity,
                "median_verts": median_verts,
                "n": n_attempts,
            }
        )
    rows.sort(key=lambda r: (r["pass_at_1"], r["morph_fidelity"] or -1.0), reverse=True)
    return rows
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_procedural_scorecard.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add app/service.py tests/test_procedural_scorecard.py
git commit -m "feat(procedural): procedural_scorecard aggregation (pass@1 + morphology fidelity)"
```

---

### Task 2: `/procedural` page + `/api/procedural.json` + nav

**Files:**

- Modify: `app/main.py` (add two routes near the `/coverage` routes at lines 575-595)
- Create: `app/templates/procedural.html`
- Modify: `app/templates/base.html:29` (add a nav link after the Coverage link)
- Test: `tests/test_procedural_page.py` (create)

**Interfaces:**

- Consumes: `service.procedural_scorecard(db) -> list[dict]` (from Task 1, keys: `model`, `attempts`, `valid`, `pass_at_1`, `morph_correct`, `morph_assessable`, `morph_fidelity`, `median_verts`, `n`); module-level `templates`, `service`, `app`, `HTMLResponse`, `Request`, `Depends`, `get_db` already in `app/main.py`.
- Produces: `GET /procedural` (HTML), `GET /api/procedural.json` (JSON list of scorecard dicts).

- [ ] **Step 1: Write the failing test**

Create `tests/test_procedural_page.py`:

```python
"""/procedural HTML page + /api/procedural.json render and expose the scorecard."""

from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app
from app.models import Category, CommissionAttempt, Generator, Task

client = TestClient(app)


def setup_module(_m):
    init_db()


def _seed_one() -> str:
    with SessionLocal() as db:
        tag = uuid.uuid4().hex
        name = f"proc-page-{tag}"
        g = Generator(slug=f"pp-{tag}", name=name, kind="model", paradigm="procedural_llm")
        cat = Category(slug=f"pp-cat-{tag}", name="C")
        db.add_all([g, cat])
        db.flush()
        t = Task(category_id=cat.id, title=f"pp-{tag}", prompt="p")
        db.add(t)
        db.flush()
        db.add(
            CommissionAttempt(
                task_id=t.id, model_id=name, generator_id=g.id, status="ok",
                mesh_stats_json=json.dumps({"vertices": 42}),
            )
        )
        db.commit()
        return name


def test_procedural_page_renders_and_names_model():
    name = _seed_one()
    r = client.get("/procedural")
    assert r.status_code == 200
    body = r.text
    assert name in body  # models are named, not anonymized
    assert "experimental" in body.lower()  # fidelity caveat present
    assert "pass@1" in body


def test_procedural_json_shape():
    name = _seed_one()
    r = client.get("/api/procedural.json")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    row = next(d for d in data if d["model"] == name)
    for k in (
        "model",
        "attempts",
        "valid",
        "pass_at_1",
        "morph_correct",
        "morph_assessable",
        "morph_fidelity",
        "median_verts",
        "n",
    ):
        assert k in row
    assert row["pass_at_1"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_procedural_page.py -v`
Expected: FAIL — both `client.get` return 404 (routes not defined) so the assertions on status_code / body fail.

- [ ] **Step 3: Create the template**

Create `app/templates/procedural.html`:

```html
{% extends "base.html" %} {% block title %}Procedural code-gen arena · Bio 3D
Arena{% endblock %} {% block content %}
<h1>Procedural code-gen arena</h1>
<p class="lede">
  Each model authors a Blender-Python script that must run headless (Blender
  4.2) and produce a valid plant mesh.
</p>
<ul>
  <li>
    <strong>pass@1</strong> — share of tasks that ran and yielded a valid mesh.
  </li>
  <li>
    <strong>Morphology fidelity</strong> — share of judgeable traits the model
    got right. <em>Experimental / uncalibrated</em>: the VLM judge has not
    passed the Mode-C agreement (&kappa;) gate. Treat it as a relative signal,
    not certified accuracy.
  </li>
</ul>

{% if rows %}
<table class="board">
  <thead>
    <tr>
      <th>Rank</th>
      <th>Model</th>
      <th>pass@1</th>
      <th>Morphology fidelity <span class="tag">experimental</span></th>
      <th>Median verts</th>
      <th>n</th>
    </tr>
  </thead>
  <tbody>
    {% for r in rows %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ r.model }}</td>
      <td>
        {{ "%.0f%%"|format(r.pass_at_1 * 100) }}
        <small>({{ r.valid }}/{{ r.attempts }})</small>
      </td>
      <td>
        {% if r.morph_fidelity is none %}&mdash; {% else %}{{
        "%.0f%%"|format(r.morph_fidelity * 100) }}
        <small>({{ r.morph_correct }}/{{ r.morph_assessable }})</small>
        {% endif %}
      </td>
      <td>{{ r.median_verts }}</td>
      <td>{{ r.n }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p>No procedural code-gen results yet.</p>
{% endif %} {% endblock %}
```

- [ ] **Step 4: Add the routes**

In `app/main.py`, immediately after the `/api/coverage.json` route (line 595), add:

```python
@app.get("/procedural", response_class=HTMLResponse)
def procedural_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "procedural.html",
        {"rows": service.procedural_scorecard(db)},
    )


@app.get("/api/procedural.json")
def api_procedural(db: Session = Depends(get_db)):
    return service.procedural_scorecard(db)
```

- [ ] **Step 5: Add the nav link**

In `app/templates/base.html`, after the Coverage link (line 29 `<a href="/coverage">Coverage</a>`), add:

```html
<a href="/procedural">Procedural</a>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_procedural_page.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Run the full suite**

Run: `pytest -q`
Expected: all pass (no regressions).

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/templates/procedural.html app/templates/base.html tests/test_procedural_page.py
git commit -m "feat(procedural): /procedural scorecard page + /api/procedural.json + nav"
```

---

## Self-Review

**1. Spec coverage:**

- Aggregation `service.procedural_scorecard` (spec §A) → Task 1. All keys (`model`, `attempts`, `valid`, `pass_at_1`, `morph_correct`, `morph_assessable`, `morph_fidelity`, `median_verts`, `n`), the `is_assessable` mirror, the `generator_id`/`source="commissioned"` links, the `"vertices"` median, the `procedural_llm` filter, and the sort → all in Task 1.
- Routes `/procedural` + `/api/procedural.json` (spec §B) → Task 2. Nav link → Task 2 Step 5. Columns + fidelity "—" (spec §B) → template in Task 2 Step 3. Experimental copy (spec §C) → template + asserted in test.
- Empty-safe (spec Testing) → Task 1 `if not gens: return []` + `test_scorecard_empty...`; template `{% else %}` branch.
- No gaps.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code.

**3. Type consistency:** `procedural_scorecard(db) -> list[dict]` with identical key set used in Task 1 (produced), Task 2 routes (passed through), template (`r.model`, `r.pass_at_1`, `r.morph_fidelity`, `r.morph_correct`, `r.morph_assessable`, `r.valid`, `r.attempts`, `r.median_verts`, `r.n`), and both tests. `morph_fidelity` is `float | None` and every consumer (template `is none`, sort `or -1.0`, test `is None`) handles None. Consistent.
