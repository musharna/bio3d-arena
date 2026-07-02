# D-Complete (organism-level completeness metric) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reference-free, per-output metric that scores whether a generated 3D output is a complete, valid plant (taxon-expected organs present) vs an isolated fruit / partial organ / fragment — orthogonal to Chamfer and to human preference — and validate it against the project's existing human incomplete-labels.

**Architecture:** A pure per-taxon organ inventory (`organ_inventory.py`) feeds a VLM organ-presence scorer (`completeness.py`, mirroring `input_grade.grade_with_vlm`) that reads reused judge contact sheets; a pure `derive()` maps the per-organ checklist to a 4-way category + a completeness fraction; results persist in a `Completeness` model (mirroring `Metric`, one row per output) exposed via `/api/completeness.json`; a batch script scores outputs and a validation script measures agreement (κ) against human labels derived from the calibration CSVs.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Anthropic tool-use (injected client), existing `judge_render` contact sheets. Test runner: `.venv/bin/pytest`.

## Global Constraints

- **Test runner is `.venv/bin/pytest`** (NOT bare `pytest`). Baseline on master @699d700 must stay green.
- **NEVER set the `BIO3D_DATABASE_URL` env var to `study`** when running pytest — it wipes a real study DB. Just run `.venv/bin/pytest -q`.
- **Reference-free:** the score must not require a GT scan/mesh; it reads only the output's own rendered views + the authored inventory.
- **Reuse existing infra, do not reinvent:** VLM tool-use pattern from `app/input_grade.py`; contact sheets from `app/judge_render.render_contact_sheets` (condition `"turntable"` (the existing 8-azimuth contact-sheet condition in app/judge_render.CONDITIONS); do NOT invent a new condition); per-output persistence shape from the `Metric` model in `app/models.py`; `JUDGE_MODEL` from `app/judge.py`; κ from `app/calibration.cohens_kappa`; output eligibility filters `app/sourcing.is_reference_scan` + `app/sourcing.is_untextured_output`.
- **Taxon resolution:** an output's taxon = `TraitRubric.taxon` for the rubric whose `task_id == output.task_id` (same as `scripts/trait_judge.enumerate_work`). The 6 covered taxa are exactly the keys of `app.trait_morphology.MORPHOLOGY_TRAITS`: `"Solanum lycopersicum"`, `"Zea mays"`, `"Pinus sylvestris"`, `"Rosa"`, `"Glycine max"`, `"Arabidopsis thaliana"`.
- **Framing:** organism-level missing-organ axis, NOT generic "plausibility." Required organs are the vegetative body (`vegetative_axis` + `foliage`); the reproductive organ is `optional` so a lone fruit registers as an isolated organ, not a "complete plant."
- **No arena wiring in v1:** no board/UI, no vote-pool gating, no matchmaking change. `/api/completeness.json` is data-only.
- **Test pattern:** follow `tests/test_trait_judge.py` — `from app.database import SessionLocal, init_db`, `setup_module(_m): init_db()`, seed via `with SessionLocal() as db:`, inject fakes for the VLM client and the sheet renderer.

---

### Task 1: Per-taxon organ inventory

**Files:**

- Create: `app/organ_inventory.py`
- Test: `tests/test_organ_inventory.py`

**Interfaces:**

- Produces: `Organ(key: str, visual: str, required: bool)` and `TaxonInventory(taxon: str, organs: tuple[Organ, ...])` (frozen dataclasses); `ORGAN_INVENTORY: dict[str, TaxonInventory]` keyed by the 6 taxon strings; `inventory_for(taxon: str) -> TaxonInventory | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_organ_inventory.py
from app.organ_inventory import ORGAN_INVENTORY, inventory_for


def test_all_six_taxa_present():
    assert set(ORGAN_INVENTORY) == {
        "Solanum lycopersicum", "Zea mays", "Pinus sylvestris",
        "Rosa", "Glycine max", "Arabidopsis thaliana",
    }


def test_every_taxon_has_required_vegetative_body_and_optional_reproductive():
    for taxon, inv in ORGAN_INVENTORY.items():
        keys = {o.key for o in inv.organs}
        assert {"vegetative_axis", "foliage"} <= keys, taxon
        # vegetative body is required; at least one reproductive organ, and it is optional
        req = {o.key for o in inv.organs if o.required}
        assert req == {"vegetative_axis", "foliage"}, taxon
        assert any(not o.required for o in inv.organs), taxon
        # every organ has a non-empty visual descriptor (a completeness read must be visual)
        assert all(o.visual.strip() for o in inv.organs), taxon


def test_inventory_for_unknown_taxon_is_none():
    assert inventory_for("Homo sapiens") is None
    assert inventory_for("Zea mays").taxon == "Zea mays"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_organ_inventory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.organ_inventory'`.

- [ ] **Step 3: Write the module**

```python
# app/organ_inventory.py
"""Authored per-taxon expected-organ inventories for the organism-level completeness metric.

Required organs = the vegetative body (a plant is "complete" if it has an axis + foliage).
The reproductive organ is OPTIONAL so a lone fruit/cone/pod registers as an isolated organ,
not a complete plant. Visual descriptors are image-judgeable phrases for the VLM checklist.
Taxon keys MUST match app.trait_morphology.MORPHOLOGY_TRAITS (== TraitRubric.taxon)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Organ:
    key: str
    visual: str
    required: bool


@dataclass(frozen=True)
class TaxonInventory:
    taxon: str
    organs: tuple[Organ, ...]


def _inv(taxon: str, axis: str, foliage: str, repro_key: str, repro: str) -> TaxonInventory:
    return TaxonInventory(
        taxon=taxon,
        organs=(
            Organ("vegetative_axis", axis, True),
            Organ("foliage", foliage, True),
            Organ(repro_key, repro, False),
        ),
    )


ORGAN_INVENTORY: dict[str, TaxonInventory] = {
    "Solanum lycopersicum": _inv(
        "Solanum lycopersicum", "an upright central green stem",
        "compound green leaves along the stem", "reproductive_fruit",
        "round red/green fleshy berries",
    ),
    "Zea mays": _inv(
        "Zea mays", "a tall single vertical stalk",
        "long linear strap-like blades along the stalk", "reproductive_inflorescence",
        "a terminal tassel and/or a lateral ear",
    ),
    "Pinus sylvestris": _inv(
        "Pinus sylvestris", "a woody trunk with branches",
        "needle leaves in clusters on the branches", "reproductive_cone",
        "egg/cone-shaped woody cones",
    ),
    "Rosa": _inv(
        "Rosa", "a thorny woody stem",
        "pinnate serrated green leaves", "reproductive_flower_hip",
        "a flower and/or a rounded fleshy rose hip",
    ),
    "Glycine max": _inv(
        "Glycine max", "an erect branching stem",
        "trifoliate leaves (leaves with three leaflets)", "reproductive_pod",
        "narrow fuzzy seed pods",
    ),
    "Arabidopsis thaliana": _inv(
        "Arabidopsis thaliana", "a slender upright flowering bolt/stalk",
        "a basal rosette of leaves", "reproductive_silique",
        "thin elongated upright siliques along the stem",
    ),
}


def inventory_for(taxon: str) -> TaxonInventory | None:
    return ORGAN_INVENTORY.get(taxon)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_organ_inventory.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/organ_inventory.py tests/test_organ_inventory.py
git commit -m "feat(completeness): per-taxon expected-organ inventory (6 Mode-C taxa)"
```

---

### Task 2: Category + score derivation (pure)

**Files:**

- Create: `app/completeness.py` (this task adds `derive` only; later tasks extend the same file)
- Test: `tests/test_completeness_derive.py`

**Interfaces:**

- Consumes: `TaxonInventory`, `Organ` from Task 1.
- Produces: `derive(inventory: TaxonInventory, organs_present: list[dict]) -> tuple[str, float]` where each `organs_present` entry is `{"key": str, "status": "present"|"absent"|"uncertain"}`. Returns `(category, score)`; `category ∈ {"complete","partial-organism","isolated-organ","fragment"}`; `score = required-present / required-total ∈ [0,1]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_derive.py
from app.completeness import derive
from app.organ_inventory import inventory_for

INV = inventory_for("Solanum lycopersicum")  # required: vegetative_axis, foliage; optional: reproductive_fruit


def _p(*present_keys):
    # build organs_present marking listed keys present, the rest absent
    keys = ["vegetative_axis", "foliage", "reproductive_fruit"]
    return [{"key": k, "status": "present" if k in present_keys else "absent"} for k in keys]


def test_complete_when_all_required_present():
    assert derive(INV, _p("vegetative_axis", "foliage")) == ("complete", 1.0)
    assert derive(INV, _p("vegetative_axis", "foliage", "reproductive_fruit")) == ("complete", 1.0)


def test_partial_organism_when_two_present_but_a_required_absent():
    # foliage + fruit present, axis absent -> present_count 2, required axis missing
    cat, score = derive(INV, _p("foliage", "reproductive_fruit"))
    assert cat == "partial-organism"
    assert score == 0.5


def test_isolated_organ_when_exactly_one_present():
    assert derive(INV, _p("reproductive_fruit")) == ("isolated-organ", 0.0)  # lone fruit
    assert derive(INV, _p("vegetative_axis")) == ("isolated-organ", 0.5)     # lone stem


def test_fragment_when_none_present():
    assert derive(INV, _p()) == ("fragment", 0.0)


def test_uncertain_never_upgrades():
    organs = [
        {"key": "vegetative_axis", "status": "present"},
        {"key": "foliage", "status": "uncertain"},
        {"key": "reproductive_fruit", "status": "absent"},
    ]
    cat, score = derive(INV, organs)  # only 1 present -> isolated-organ, uncertain foliage not counted
    assert cat == "isolated-organ"
    assert score == 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_completeness_derive.py -v`
Expected: FAIL — `ImportError: cannot import name 'derive'` (module doesn't exist yet).

- [ ] **Step 3: Write `derive` in a new `app/completeness.py`**

```python
# app/completeness.py
"""Organism-level biological completeness metric: VLM organ-presence read of a generated
plant's rendered views against its taxon's expected-organ inventory, plus category/score
derivation. Reference-free (no GT). Mirrors the app.input_grade VLM tool-use pattern."""

from __future__ import annotations

from app.organ_inventory import TaxonInventory


def derive(inventory: TaxonInventory, organs_present: list[dict]) -> tuple[str, float]:
    """Map a per-organ present/absent/uncertain checklist to (category, score).

    Required organs = the vegetative body; score = required-present / required-total.
    Categories are total + mutually exclusive over present_count in {0, 1, >=2}."""
    status = {o["key"]: o.get("status") for o in organs_present}
    required = [o.key for o in inventory.organs if o.required]
    req_present = sum(1 for k in required if status.get(k) == "present")
    score = req_present / len(required) if required else 0.0
    present_count = sum(1 for v in status.values() if v == "present")

    if present_count == 0:
        category = "fragment"
    elif present_count == 1:
        category = "isolated-organ"
    elif req_present == len(required):
        category = "complete"
    else:
        category = "partial-organism"
    return category, score
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_completeness_derive.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/completeness.py tests/test_completeness_derive.py
git commit -m "feat(completeness): category + score derivation from organ checklist"
```

---

### Task 3: VLM organ-presence scorer

**Files:**

- Modify: `app/completeness.py` (add `COMPLETENESS_TOOL`, `_build_messages`, `_parse`, `score_completeness`)
- Test: `tests/test_completeness_scorer.py`

**Interfaces:**

- Consumes: `TaxonInventory` (Task 1), `JUDGE_MODEL` from `app.judge`.
- Produces: `score_completeness(client, sheet_png: bytes, *, inventory: TaxonInventory) -> dict` returning `{"organs_present": [{"key","status"}...], "note": str}`. Raises `ValueError` if the response has no `record_completeness` tool_use block.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_scorer.py
import pytest

from app.completeness import score_completeness
from app.organ_inventory import inventory_for

INV = inventory_for("Pinus sylvestris")


class _Block:
    def __init__(self, name, inp):
        self.type = "tool_use"
        self.name = name
        self.input = inp


class _Resp:
    def __init__(self, content):
        self.content = content


class _FakeClient:
    """Mimics anthropic client.messages.create -> response with .content blocks."""
    def __init__(self, resp):
        self._resp = resp
        self.messages = self

    def create(self, **kwargs):
        return self._resp


def test_parses_record_completeness_block():
    payload = {
        "organs_present": [
            {"key": "vegetative_axis", "status": "present"},
            {"key": "foliage", "status": "present"},
            {"key": "reproductive_cone", "status": "absent"},
        ],
        "note": "young pine, no cones",
    }
    client = _FakeClient(_Resp([_Block("record_completeness", payload)]))
    out = score_completeness(client, b"\x89PNG_fake", inventory=INV)
    assert out["organs_present"][0]["key"] == "vegetative_axis"
    assert out["note"] == "young pine, no cones"


def test_raises_when_no_tool_block():
    client = _FakeClient(_Resp([]))
    with pytest.raises(ValueError):
        score_completeness(client, b"\x89PNG_fake", inventory=INV)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_completeness_scorer.py -v`
Expected: FAIL — `ImportError: cannot import name 'score_completeness'`.

- [ ] **Step 3: Add the scorer to `app/completeness.py`**

Add these imports at the top of `app/completeness.py` (below the existing `from app.organ_inventory import TaxonInventory`):

```python
import base64

from app.judge import JUDGE_MODEL
```

Then append:

```python
COMPLETENESS_TOOL = {
    "name": "record_completeness",
    "description": "Record which expected organs are visible in the rendered plant model.",
    "input_schema": {
        "type": "object",
        "properties": {
            "organs_present": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "status": {"type": "string", "enum": ["present", "absent", "uncertain"]},
                    },
                    "required": ["key", "status"],
                },
            },
            "note": {"type": "string"},
        },
        "required": ["organs_present", "note"],
    },
}


def _img_block(png: bytes) -> dict:
    b64 = base64.b64encode(png).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _build_messages(png: bytes, inventory: TaxonInventory) -> list[dict]:
    lines = "\n".join(f"- {o.key}: {o.visual}" for o in inventory.organs)
    text = (
        f"This is a contact sheet of a generated 3D model of the plant {inventory.taxon}, "
        "rendered from several angles. For EACH expected organ below, mark whether it is "
        "visibly present in the model (present / absent / uncertain). Judge only what you can "
        "see; a rendering of a single detached organ should mark the others absent.\n\n"
        f"Expected organs:\n{lines}\n\nThen call record_completeness."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img_block(png)]}]


def _parse(response) -> dict:
    for block in getattr(response, "content", []):
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "record_completeness":
            inp = block.input
            return {"organs_present": inp.get("organs_present", []), "note": inp.get("note", "")}
    raise ValueError("no record_completeness tool_use block in response")


def score_completeness(client, sheet_png: bytes, *, inventory: TaxonInventory) -> dict:
    """One VLM call over the contact sheet; returns the parsed organ checklist + note."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=500,
        tools=[COMPLETENESS_TOOL],
        tool_choice={"type": "tool", "name": "record_completeness"},
        messages=_build_messages(sheet_png, inventory),
    )
    return _parse(resp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_completeness_scorer.py tests/test_completeness_derive.py -v`
Expected: PASS (7 tests total).

- [ ] **Step 5: Commit**

```bash
git add app/completeness.py tests/test_completeness_scorer.py
git commit -m "feat(completeness): VLM organ-presence scorer (record_completeness tool-use)"
```

---

### Task 4: `Completeness` persistence model + read API

**Files:**

- Modify: `app/models.py` (add `Completeness` model, after the `Metric` class)
- Modify: `app/service.py` (add `completeness_rows`)
- Modify: `app/main.py` (add `/api/completeness.json` route)
- Modify: `app/completeness.py` (add `upsert_completeness`)
- Test: `tests/test_completeness_persistence.py`

**Interfaces:**

- Consumes: `ModelOutput` (`model_output` table), `Base`/session from `app.database`.
- Produces: `Completeness` model (table `completeness`, one row per output, unique `output_id`); `upsert_completeness(db, output_id, *, category, score, checklist, judge_model, scorer_version) -> Completeness`; `service.completeness_rows(db) -> list[dict]`; `GET /api/completeness.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_persistence.py
import json

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.completeness import upsert_completeness
from app import service


def setup_module(_m):
    init_db()


def _seed_output(db) -> int:
    cat = Category(slug="tomato-comp-test", name="Solanum lycopersicum")
    gen = Generator(name="gen-comp-test", paradigm="")
    db.add_all([cat, gen])
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="x.glb")
    db.add(out)
    db.flush()
    return out.id


def test_upsert_is_one_row_per_output_and_overwrites():
    with SessionLocal() as db:
        oid = _seed_output(db)
        upsert_completeness(db, oid, category="fragment", score=0.0,
                            checklist={"organs_present": [], "note": "blob"},
                            judge_model="m", scorer_version="v1")
        db.commit()
        upsert_completeness(db, oid, category="complete", score=1.0,
                            checklist={"organs_present": [], "note": "ok"},
                            judge_model="m", scorer_version="v1")
        db.commit()
        rows = [r for r in service.completeness_rows(db) if r["output_id"] == oid]
        assert len(rows) == 1
        assert rows[0]["category"] == "complete"
        assert rows[0]["score"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_completeness_persistence.py -v`
Expected: FAIL — `ImportError: cannot import name 'upsert_completeness'`.

- [ ] **Step 3: Add the `Completeness` model to `app/models.py`**

Immediately AFTER the `Metric` class (which ends with the `computed` column), add:

```python
class Completeness(Base):
    """Organism-level completeness score for one ModelOutput (reference-free VLM read).
    One row per output (latest); rescoring overwrites. checklist_json holds the raw
    per-organ statuses + note for audit/explainability."""

    __tablename__ = "completeness"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(20), default="")  # complete|partial-organism|isolated-organ|fragment
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    checklist_json: Mapped[str] = mapped_column(Text, default="{}")
    judge_model: Mapped[str] = mapped_column(String(128), default="")
    scorer_version: Mapped[str] = mapped_column(String(64), default="")
    computed: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

(Confirm `ForeignKey`, `String`, `Float`, `Text`, `DateTime`, `Mapped`, `mapped_column`, `dt`, `_utcnow` are already imported/defined at the top of `models.py` — they are used by the surrounding models like `Metric`.)

- [ ] **Step 4: Add `upsert_completeness` to `app/completeness.py`**

Add this import near the top of `app/completeness.py`:

```python
import json
```

Append:

```python
def upsert_completeness(db, output_id: int, *, category: str, score: float | None,
                        checklist: dict, judge_model: str, scorer_version: str):
    """Insert or overwrite the single Completeness row for an output. Caller commits."""
    from app.models import Completeness

    row = db.query(Completeness).filter_by(output_id=output_id).one_or_none()
    if row is None:
        row = Completeness(output_id=output_id)
        db.add(row)
    row.category = category
    row.score = score
    row.checklist_json = json.dumps(checklist)
    row.judge_model = judge_model
    row.scorer_version = scorer_version
    return row
```

- [ ] **Step 5: Add `completeness_rows` to `app/service.py`**

Append to `app/service.py`:

```python
def completeness_rows(db) -> list[dict]:
    """Per-output completeness rows for /api/completeness.json (taxon via the output's
    task rubric; None when no rubric)."""
    from app.models import Completeness, ModelOutput, TraitRubric

    out = []
    for c in db.query(Completeness).all():
        mo = db.get(ModelOutput, c.output_id)
        taxon = None
        if mo is not None:
            rubric = db.query(TraitRubric).filter_by(task_id=mo.task_id).first()
            taxon = rubric.taxon if rubric else None
        out.append({
            "output_id": c.output_id,
            "taxon": taxon,
            "generator_id": mo.generator_id if mo else None,
            "category": c.category,
            "score": c.score,
        })
    return out
```

- [ ] **Step 6: Add the `/api/completeness.json` route to `app/main.py`**

Next to the other `/api/*.json` routes (e.g. after `api_procedural`), add:

```python
@app.get("/api/completeness.json")
def api_completeness(db: Session = Depends(get_db)):
    return service.completeness_rows(db)
```

- [ ] **Step 7: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_completeness_persistence.py -v` → PASS.
Run: `.venv/bin/pytest -q` → no regressions. (Do NOT set `BIO3D_DATABASE_URL=study`.)

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/completeness.py app/service.py app/main.py tests/test_completeness_persistence.py
git commit -m "feat(completeness): Completeness model + upsert + /api/completeness.json"
```

---

### Task 5: Enumeration + batch scoring driver

**Files:**

- Modify: `app/completeness.py` (add `enumerate_completeness_work`, `score_outputs`)
- Create: `scripts/score_completeness.py`
- Test: `tests/test_completeness_batch.py`

**Interfaces:**

- Consumes: `TraitRubric`/`Task`/`ModelOutput`; `app.sourcing.is_reference_scan`, `app.sourcing.is_untextured_output`; `inventory_for` (Task 1); `score_completeness` (Task 3); `derive` (Task 2); `upsert_completeness` (Task 4).
- Produces: `enumerate_completeness_work(db, task_ids) -> list[dict]` (rows `{output_id, taxon}`); `score_outputs(db, work, *, client, sheet_for, scorer_version) -> dict` summary `{scored, skipped_no_inventory, errors, failures}` where `sheet_for(output_id) -> bytes` is an injected sheet provider.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_batch.py
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task, TraitRubric, Completeness
from app.completeness import enumerate_completeness_work, score_outputs


def setup_module(_m):
    init_db()


def _seed(db):
    cat = Category(slug="pine-batch-test", name="Pinus sylvestris")
    gen = Generator(name="gen-batch-test", paradigm="")
    db.add_all([cat, gen])
    db.flush()
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    db.add(TraitRubric(task_id=task.id, taxon="Pinus sylvestris", traits_json="[]"))
    out = ModelOutput(task_id=task.id, generator_id=gen.id, asset_path="p.glb")
    db.add(out)
    db.flush()
    return task.id, out.id


def test_enumerate_and_score_writes_completeness():
    with SessionLocal() as db:
        tid, oid = _seed(db)
        db.commit()
        work = enumerate_completeness_work(db, [tid])
        assert {"output_id": oid, "taxon": "Pinus sylvestris"} in work

        class _FakeClient:
            def __init__(self):
                self.messages = self

            def create(self, **kw):
                class B:
                    type = "tool_use"; name = "record_completeness"
                    input = {"organs_present": [
                        {"key": "vegetative_axis", "status": "present"},
                        {"key": "foliage", "status": "present"},
                        {"key": "reproductive_cone", "status": "absent"}],
                        "note": "ok"}
                class R:
                    content = [B()]
                return R()

        summary = score_outputs(db, work, client=_FakeClient(),
                                sheet_for=lambda oid: b"\x89PNG", scorer_version="t1")
        db.commit()
        assert summary["scored"] == 1
        row = db.query(Completeness).filter_by(output_id=oid).one()
        assert row.category == "complete"
        assert row.score == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_completeness_batch.py -v`
Expected: FAIL — `ImportError: cannot import name 'enumerate_completeness_work'`.

- [ ] **Step 3: Add enumeration + orchestration to `app/completeness.py`**

Append:

```python
def enumerate_completeness_work(db, task_ids) -> list[dict]:
    """One row per eligible output (non-gold, non-reference-scan, non-untextured) of tasks
    that HAVE a TraitRubric with an inventory-covered taxon. Mirrors trait_judge.enumerate_work."""
    from app.models import Task, TraitRubric
    from app.organ_inventory import inventory_for
    from app.sourcing import is_reference_scan, is_untextured_output

    items = []
    for tid in task_ids:
        rubric = db.query(TraitRubric).filter_by(task_id=tid).first()
        if rubric is None or inventory_for(rubric.taxon) is None:
            continue
        task = db.get(Task, tid)
        if task is None:
            continue
        for out in task.outputs:
            if out.is_gold or is_reference_scan(out.source) or is_untextured_output(out):
                continue
            items.append({"output_id": out.id, "taxon": rubric.taxon})
    return items


def score_outputs(db, work, *, client, sheet_for, scorer_version: str) -> dict:
    """Score each work row: get its contact sheet (injected sheet_for), VLM-check, derive,
    upsert. Fail-loud per output (recorded, loop continues). Caller commits."""
    from app.organ_inventory import inventory_for

    scored = skipped = errors = 0
    failures = []
    for item in work:
        inv = inventory_for(item["taxon"])
        if inv is None:
            skipped += 1
            continue
        try:
            png = sheet_for(item["output_id"])
            result = score_completeness(client, png, inventory=inv)
            category, score = derive(inv, result["organs_present"])
            upsert_completeness(db, item["output_id"], category=category, score=score,
                                checklist=result, judge_model=JUDGE_MODEL,
                                scorer_version=scorer_version)
            scored += 1
        except Exception as e:  # fail-loud per output, do not abort the batch
            errors += 1
            failures.append({"output_id": item["output_id"], "error": repr(e)})
    return {"scored": scored, "skipped_no_inventory": skipped, "errors": errors, "failures": failures}
```

- [ ] **Step 4: Create the batch driver `scripts/score_completeness.py`**

```python
# scripts/score_completeness.py
"""Batch-score organism-level completeness for outputs. Renders (or reuses) a turntable (the
existing 8-azimuth contact-sheet condition in app/judge_render.CONDITIONS) contact sheet per
output, VLM-checks organ presence, persists a Completeness row. Build the Anthropic
client from ANTHROPIC_API_KEY (as scripts/judge_vlm.py does). Never set BIO3D_DATABASE_URL=study."""

from __future__ import annotations

import argparse
import base64
import sys

from app.database import SessionLocal, init_db
from app.completeness import enumerate_completeness_work, score_outputs
from app.judge_render import contact_sheet_path, render_contact_sheets
from app import config

SCORER_VERSION = "completeness-v1"
CONDITION = "turntable"


def _sheet_provider(db, capture_multi):
    """Render (idempotently) then read the turntable contact-sheet PNG bytes for an output."""
    import os

    def sheet_for(output_id: int) -> bytes:
        render_contact_sheets(db, [output_id], CONDITION, capture_multi=capture_multi)
        path = os.path.join(config.ASSET_DIR, contact_sheet_path(output_id, CONDITION))
        with open(path, "rb") as f:
            return f.read()

    return sheet_for


def _build_client():
    import os
    import anthropic

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _capture_multi():
    # The Playwright multi-angle GLB capture used by the judge pipeline. Import lazily so
    # unit tests never need a browser. Reuse the same capture the judge batch uses.
    from scripts.judge_vlm import build_capture_multi  # adjust-on-contact: judge's capture factory

    return build_capture_multi()


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-score organism-level completeness.")
    ap.add_argument("--tasks", default="", help="comma task ids (default: all with a rubric)")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        from app.models import Task, TraitRubric

        if args.tasks:
            task_ids = [int(x) for x in args.tasks.split(",") if x.strip()]
        else:
            task_ids = [t.id for t in db.query(Task).join(TraitRubric, TraitRubric.task_id == Task.id)]
        work = enumerate_completeness_work(db, task_ids)
        sheet_for = _sheet_provider(db, _capture_multi())
        summary = score_outputs(db, work, client=_build_client(),
                                sheet_for=sheet_for, scorer_version=SCORER_VERSION)
        db.commit()
    print(summary)
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

Note (adjust-on-contact): `_capture_multi` reuses the judge pipeline's Playwright capture. Confirm the exact factory name/location in `scripts/judge_vlm.py` (grep for the function passed as `capture_multi=` into `render_contact_sheets`) and import that; the injected `sheet_for` seam keeps this out of the unit tests.

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/pytest tests/test_completeness_batch.py -v` → PASS.
Run: `.venv/bin/pytest -q` → no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/completeness.py scripts/score_completeness.py tests/test_completeness_batch.py
git commit -m "feat(completeness): output enumeration + batch scoring driver"
```

---

### Task 6: Validation harness (κ vs human labels + baseline contrast)

**Files:**

- Create: `app/completeness_validation.py` (pure helpers: GT keyword mapping + metrics)
- Create: `scripts/validate_completeness.py` (runs it over the calibration CSVs + persisted rows, writes results)
- Test: `tests/test_completeness_validation.py`

**Interfaces:**

- Consumes: `app.calibration.cohens_kappa`.
- Produces: `map_note_to_category(human_verdict: str, note: str) -> str | None` (None = ambiguous/drop); `gt_by_output(rows: list[dict]) -> dict[int, str]` (aggregate trait-level rows per output_id → one category); `agreement(pred: dict[int,str], gt: dict[int,str]) -> dict` returning `{"n","binary_kappa","fourway_kappa","isolated_recall","dropped"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_validation.py
from app.completeness_validation import map_note_to_category, gt_by_output, agreement


def test_map_note_keywords():
    assert map_note_to_category("", "only a fruit, no plant") == "isolated-organ"
    assert map_note_to_category("", "partial plant, missing leaves") == "partial-organism"
    assert map_note_to_category("", "not a plant / junk") == "fragment"
    assert map_note_to_category("ok", "looks like a whole tomato plant") == "complete"
    assert map_note_to_category("", "") is None  # ambiguous -> drop


def test_gt_by_output_takes_worst_incompleteness_per_output():
    rows = [
        {"output_id": 1, "human_verdict": "", "note": "only a fruit"},
        {"output_id": 1, "human_verdict": "", "note": "looks fine"},
        {"output_id": 2, "human_verdict": "ok", "note": "whole plant"},
    ]
    gt = gt_by_output(rows)
    assert gt[1] == "isolated-organ"   # any incompleteness flag on an output wins
    assert gt[2] == "complete"


def test_agreement_computes_binary_kappa_and_isolated_recall():
    gt = {1: "isolated-organ", 2: "complete", 3: "complete", 4: "isolated-organ"}
    pred = {1: "isolated-organ", 2: "complete", 3: "complete", 4: "complete"}  # miss 4
    m = agreement(pred, gt)
    assert m["n"] == 4
    assert m["isolated_recall"] == 0.5  # caught 1 of 2 isolated
    assert -1.0 <= m["binary_kappa"] <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_completeness_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.completeness_validation'`.

- [ ] **Step 3: Write `app/completeness_validation.py`**

```python
# app/completeness_validation.py
"""Validate the completeness metric against human labels derived from the trait-level
calibration CSVs. The GT is derived from free-text notes via an auditable keyword table;
ambiguous outputs are dropped (counted), never coerced to complete."""

from __future__ import annotations

from app.calibration import cohens_kappa

# Order matters: the FIRST matching bucket wins (worst incompleteness first).
_KEYWORDS = [
    ("fragment", ["not a plant", "junk", "garbage", "blob", "unrecognizable", "wtf"]),
    ("isolated-organ", ["only a fruit", "just a fruit", "isolated", "single organ",
                         "only a leaf", "detached", "lone "]),
    ("partial-organism", ["partial", "incomplete", "missing", "fragment of", "half a"]),
]
_COMPLETE_HINTS = ["whole", "complete", "full plant", "looks fine", "looks good", "ok", "correct"]


def map_note_to_category(human_verdict: str, note: str) -> str | None:
    text = f"{human_verdict} {note}".lower()
    if not text.strip():
        return None
    for cat, kws in _KEYWORDS:
        if any(k in text for k in kws):
            return cat
    if any(h in text for h in _COMPLETE_HINTS):
        return "complete"
    return None  # ambiguous -> drop from the eval set


_SEVERITY = {"fragment": 0, "isolated-organ": 1, "partial-organism": 2, "complete": 3}


def gt_by_output(rows: list[dict]) -> dict[int, str]:
    """Aggregate trait-level rows to one category per output_id: the WORST (lowest-severity)
    non-None mapped label across that output's rows. Outputs with only ambiguous rows drop."""
    worst: dict[int, str] = {}
    for r in rows:
        cat = map_note_to_category(r.get("human_verdict", ""), r.get("note", ""))
        if cat is None:
            continue
        oid = r["output_id"]
        if oid not in worst or _SEVERITY[cat] < _SEVERITY[worst[oid]]:
            worst[oid] = cat
    return worst


def _binary(cat: str) -> str:
    return "complete" if cat == "complete" else "incomplete"


def agreement(pred: dict[int, str], gt: dict[int, str]) -> dict:
    """κ (binary complete/incomplete + full 4-way) and isolated-organ recall over the
    outputs present in BOTH pred and gt."""
    oids = [o for o in gt if o in pred]
    dropped = len(gt) - len(oids)
    g4 = [gt[o] for o in oids]
    p4 = [pred[o] for o in oids]
    gb = [_binary(c) for c in g4]
    pb = [_binary(c) for c in p4]
    iso = [o for o in oids if gt[o] == "isolated-organ"]
    iso_hit = sum(1 for o in iso if pred[o] == "isolated-organ")
    return {
        "n": len(oids),
        "binary_kappa": cohens_kappa(gb, pb),
        "fourway_kappa": cohens_kappa(g4, p4),
        "isolated_recall": (iso_hit / len(iso)) if iso else None,
        "dropped": dropped,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_completeness_validation.py -v` → PASS (3 tests).

- [ ] **Step 5: Write `scripts/validate_completeness.py`**

```python
# scripts/validate_completeness.py
"""Validate the completeness metric: derive GT categories from the trait-level calibration
CSVs, compare against persisted Completeness rows, report binary+4-way kappa and isolated
recall, and print the auditable GT mapping. Writes docs/results/2026-07-01-completeness-
validation-results.md. Never set BIO3D_DATABASE_URL=study."""

from __future__ import annotations

import csv
import glob
import sys

from app.database import SessionLocal, init_db
from app.completeness_validation import gt_by_output, agreement, map_note_to_category


def _load_calibration_rows() -> list[dict]:
    rows = []
    for path in glob.glob("data/study/calibration_labels*.csv"):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if "output_id" not in r:
                    continue
                try:
                    r["output_id"] = int(r["output_id"])
                except (TypeError, ValueError):
                    continue
                rows.append(r)
    return rows


def main() -> int:
    init_db()
    cal = _load_calibration_rows()
    gt = gt_by_output(cal)
    with SessionLocal() as db:
        from app.models import Completeness

        pred = {c.output_id: c.category for c in db.query(Completeness).all()}
    m = agreement(pred, gt)
    lines = [
        "# Completeness metric — validation results",
        "",
        f"- eval outputs (in both GT and pred): {m['n']}",
        f"- binary complete/incomplete kappa: {m['binary_kappa']}",
        f"- 4-way category kappa: {m['fourway_kappa']}",
        f"- isolated-organ recall: {m['isolated_recall']}",
        f"- GT outputs with no prediction (dropped): {m['dropped']}",
        "",
        "## Auditable GT mapping (note -> category)",
    ]
    for r in cal[:200]:
        cat = map_note_to_category(r.get("human_verdict", ""), r.get("note", ""))
        lines.append(f"- output {r['output_id']}: {(r.get('note') or '')[:60]!r} -> {cat}")
    with open("docs/results/2026-07-01-completeness-validation-results.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(m)
    # PASS gate: binary kappa >= 0.6 (4-way may be experimental).
    bk = m["binary_kappa"]
    return 0 if (bk is not None and bk >= 0.6) else 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the validation live + full suite**

Run: `.venv/bin/pytest tests/test_completeness_validation.py -q` → PASS.
Run: `.venv/bin/pytest -q` → no regressions.
Then, once outputs have been scored (Task 5's driver has run against a seeded DB with completeness rows), run `.venv/bin/python scripts/validate_completeness.py` and record the κ numbers in the results doc. Report whether binary κ ≥ 0.6 (PASS) or the 4-way is experimental (fallback). (This live step is the metric's validation — it may reveal the metric needs prompt/inventory iteration; that is expected and in-scope.)

- [ ] **Step 7: Commit**

```bash
git add app/completeness_validation.py scripts/validate_completeness.py tests/test_completeness_validation.py docs/results/2026-07-01-completeness-validation-results.md
git commit -m "feat(completeness): validation harness (kappa vs human labels + isolated recall)"
```

---

## Self-Review

**Spec coverage:**

- Organ inventory (authored per-taxon, 6 Mode-C taxa, required vegetative body + optional reproductive) → Task 1. ✓
- VLM expected-organ checklist over reused judge contact sheets → Task 3 + Task 5's `sheet_for` (turntable). ✓
- Derivation → 4-way category + fraction (total/MECE rules) → Task 2. ✓
- Persistence mirroring `Metric` (one row/output) + `/api/completeness.json` (data-only, no board) → Task 4. ✓
- Batch scoring pass with the trait_judge eligibility filters + taxon resolution → Task 5. ✓
- Validation: GT derived from trait-level calibration notes (auditable mapping, drop-and-count), binary+4-way κ (reuse `cohens_kappa`), isolated-organ recall; success bar binary κ≥0.6 with 4-way experimental fallback → Task 6. ✓
- Real-execution check (live VLM read on known outputs) → Task 5 driver + Task 6 live validation run (Step 6). ✓
- YAGNI (no board/gating/D-Gen/extra taxa) → honored; `/api/completeness.json` is data-only. ✓
- Geometry baseline contrast (spec's moat-proof): PARTIALLY covered — the validation reports the metric's own κ/recall but the plan does not implement a separate geometry-baseline comparison. **Gap noted:** the baseline contrast (show a no-fragmentation/Chamfer signal fails to separate the isolated-organ cases) is deferred to the validation write-up as a manual analysis in Step 6, not automated. Acceptable for v1 (the primary validated claim is the κ gate); flag for the reviewer.

**Placeholder scan:** no TBD/TODO. Two explicit adjust-on-contact points (the judge's `capture_multi` factory name in Task 5; confirming `models.py` imports in Task 4) are real integration confirmations, not logic gaps.

**Type/name consistency:** `derive(inventory, organs_present) -> (category, score)`, `score_completeness(client, sheet_png, *, inventory)`, `upsert_completeness(db, output_id, *, category, score, checklist, judge_model, scorer_version)`, `enumerate_completeness_work(db, task_ids)`, `score_outputs(db, work, *, client, sheet_for, scorer_version)`, `completeness_rows(db)`, `map_note_to_category`, `gt_by_output`, `agreement` — used identically across tasks and tests. Organ keys (`vegetative_axis`, `foliage`, `reproductive_*`) consistent between Task 1 inventory, Task 2/3 tests, and Task 5. Taxon strings match `MORPHOLOGY_TRAITS` keys. `Completeness` model/table names consistent across Tasks 4–6.
