# Mode-C Botanical-Trait Ground Truth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Mode-C — an objective scoring axis that grades a generated 3D model against a
literature-sourced per-taxon botanical-trait rubric via a calibrated VLM trait-checker.

**Architecture:** Four new create_all tables (`TraitRubric`, `TraitVerdict`, `TraitScore`,
`TraitCalibration`). A pure trait-checking core (`app/traits.py`) mirrors `app/judge.py`
(forced-tool, injected client). A resumable batch driver (`scripts/trait_judge.py`) mirrors
`scripts/judge_vlm.py` (dry-run + `--max`). Scoring + per-class κ gate live in `app/service.py`,
reusing `app/calibration.py::cohens_kappa`. Surfacing reuses the content-page + recon-board +
`/coverage` patterns. No Mode-A / Mode-B code path is modified.

**Tech Stack:** FastAPI + Jinja2 + vanilla JS, SQLAlchemy/SQLite (create_all-only), Anthropic
SDK (`claude-sonnet-4-6` via `app.judge.JUDGE_MODEL`), Playwright contact-sheet renders.

## Global Constraints

Every task's requirements implicitly include these (copied from the approved spec):

- **create_all-only schema** — add tables via ORM classes in `app/models.py`; NO migrations/ALTER.
- **Every trait carries `source_tier` (`db`|`llm`) + a non-empty `citation`.** No uncited traits.
- **Score only visual + relative-proportion classes** — allowed `trait_class` ∈
  `{habit, organ_shape, phyllotaxy, inflorescence, color, presence, proportion}`. NEVER absolute
  size or non-visual traits.
- **Verdict vocabulary is EXACTLY** `{present_correct, present_wrong, absent, not_assessable}`.
- **Score counts ONLY trait classes with `TraitCalibration.accepted == True`**; `not_assessable`
  is always excluded from numerator and denominator.
- **κ gate:** `accepted = (kappa is not None and kappa >= 0.6 and n >= MIN_N)`, `MIN_N = 20`.
- **Batch scripts:** `--dry-run` prints the uncovered call count and exits with NO API/browser
  import; `--max` caps writes; resumable via a skip key. (Mirror `scripts/judge_vlm.py`.)
- **Reuse, don't fork:** `judge_render.render_contact_sheets` (multi4), `calibration.cohens_kappa`,
  `service.generator_display_names` / `mode_a_excluded_generator_ids`. Do not touch `apply_vote`,
  `_build_comparison`, the Mode-A/Mode-B recompute paths.
- **Exclude** reference-scan + untextured outputs from trait-checking via
  `app.sourcing.is_reference_scan` / `is_untextured_output`.
- **Mode-C is labeled "experimental"** in the UI until a class passes the gate.
- **Tests run with DEFAULT env only** — NEVER set `BIO3D_DATABASE_URL`/`BIO3D_DATA_DIR` to the
  study DB when running pytest (it wipes it — known incident). `.venv/bin/python -m pytest`.

---

### Task 1: Data model — four Mode-C tables

**Files:**

- Modify: `app/models.py` (append four classes near the other per-output tables)
- Test: `tests/test_trait_models.py`

**Interfaces:**

- Produces: `TraitRubric(id, taxon, task_id, traits_json, created, updated)`,
  `TraitVerdict(id, output_id, rubric_id, trait_key, trait_class, verdict, rationale, judge_model, created)`,
  `TraitScore(id, output_id[unique], botanical_accuracy, n_scored, n_total, judge_model, updated)`,
  `TraitCalibration(id, trait_class[unique], kappa, n, accepted, updated)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trait_models.py
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import TraitCalibration, TraitRubric, TraitScore, TraitVerdict


def setup_module(_m):
    init_db()


def test_trait_tables_roundtrip():
    with SessionLocal() as db:
        r = TraitRubric(taxon="Solanum lycopersicum", task_id=None, traits_json="[]")
        db.add(r)
        db.flush()
        db.add(TraitVerdict(output_id=1, rubric_id=r.id, trait_key="habit",
                            trait_class="habit", verdict="present_correct",
                            rationale="ok", judge_model="m"))
        db.add(TraitScore(output_id=1, botanical_accuracy=0.5, n_scored=2, n_total=4,
                          judge_model="m"))
        db.add(TraitCalibration(trait_class="color", kappa=0.7, n=25, accepted=True))
        db.commit()
        assert db.query(TraitVerdict).filter_by(rubric_id=r.id).count() == 1
        assert db.query(TraitScore).filter_by(output_id=1).one().botanical_accuracy == 0.5
        assert db.query(TraitCalibration).filter_by(trait_class="color").one().accepted is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trait_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'TraitRubric'`.

- [ ] **Step 3: Implement the four model classes**

Append to `app/models.py` (uses the module's existing imports: `Mapped`, `mapped_column`,
`ForeignKey`, `String`, `Text`, `Float`, `Integer`, `Boolean`, `DateTime`, `_utcnow`):

```python
class TraitRubric(Base):
    """Literature-sourced botanical-trait rubric for one taxon. traits_json is a list of
    {key, trait_class, type, expected, visual, source_tier, citation} (see the Mode-C spec)."""

    __tablename__ = "trait_rubric"

    id: Mapped[int] = mapped_column(primary_key=True)
    taxon: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True, index=True)
    traits_json: Mapped[str] = mapped_column(Text, default="[]")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class TraitVerdict(Base):
    """One VLM verdict for one (output, trait). JudgeVote analog."""

    __tablename__ = "trait_verdict"
    __table_args__ = (
        UniqueConstraint("output_id", "trait_key", "judge_model", name="uq_trait_verdict"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(ForeignKey("model_output.id"), index=True)
    rubric_id: Mapped[int] = mapped_column(ForeignKey("trait_rubric.id"), index=True)
    trait_key: Mapped[str] = mapped_column(String(64))
    trait_class: Mapped[str] = mapped_column(String(32), index=True)
    verdict: Mapped[str] = mapped_column(String(20))  # present_correct|present_wrong|absent|not_assessable
    rationale: Mapped[str] = mapped_column(Text, default="")
    judge_model: Mapped[str] = mapped_column(String(64), default="")
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class TraitScore(Base):
    """Per-output botanical-accuracy score (calibrated classes only). Metric analog."""

    __tablename__ = "trait_score"

    id: Mapped[int] = mapped_column(primary_key=True)
    output_id: Mapped[int] = mapped_column(
        ForeignKey("model_output.id"), unique=True, index=True
    )
    botanical_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_scored: Mapped[int] = mapped_column(Integer, default=0)
    n_total: Mapped[int] = mapped_column(Integer, default=0)
    judge_model: Mapped[str] = mapped_column(String(64), default="")
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class TraitCalibration(Base):
    """Per-trait-class human↔VLM agreement gate. accepted classes count toward scores."""

    __tablename__ = "trait_calibration"

    id: Mapped[int] = mapped_column(primary_key=True)
    trait_class: Mapped[str] = mapped_column(String(32), unique=True)
    kappa: Mapped[float | None] = mapped_column(Float, nullable=True)
    n: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trait_models.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_trait_models.py
git commit -m "feat(mode-c): trait rubric/verdict/score/calibration tables"
```

---

### Task 2: VLM trait-checking core (`app/traits.py`)

**Files:**

- Create: `app/traits.py`
- Test: `tests/test_traits_core.py`

**Interfaces:**

- Consumes: `app.judge.JUDGE_MODEL`.
- Produces:
  - `SCORED_CLASSES: set[str]` — the 7 allowed trait classes.
  - `VERDICTS: set[str]` — the 4-verdict vocabulary.
  - `check_traits(client, *, species, prompt, sheet_b64, traits) -> list[dict]` where each input
    trait is `{key, trait_class, expected, ...}` and each returned dict is
    `{trait_key, trait_class, verdict, rationale}`.
  - `parse_traits(response, traits) -> list[dict]`, `build_trait_messages(...)`, `TRAITS_TOOL`.

- [ ] **Step 1: Write the failing test** (stub client, no network)

```python
# tests/test_traits_core.py
from __future__ import annotations

from app import traits


class _Block:
    type = "tool_use"
    name = "record_traits"

    def __init__(self, data):
        self.input = data


class _Resp:
    def __init__(self, data):
        self.content = [_Block(data)]


class _Client:
    def __init__(self, data):
        self._data = data
        self.messages = self

    def create(self, **kw):
        return _Resp(self._data)


def test_check_traits_parses_per_trait_verdicts():
    rubric = [
        {"key": "spadix", "trait_class": "presence", "expected": "present"},
        {"key": "leaf_shape", "trait_class": "organ_shape", "expected": "ovate"},
    ]
    client = _Client(
        {"traits": [
            {"trait_key": "spadix", "verdict": "present_correct", "rationale": "visible"},
            {"trait_key": "leaf_shape", "verdict": "absent", "rationale": "no leaves"},
        ]}
    )
    out = traits.check_traits(
        client, species="Amorphophallus", prompt="model it", sheet_b64="x", traits=rubric
    )
    by = {o["trait_key"]: o for o in out}
    assert by["spadix"]["verdict"] == "present_correct"
    assert by["spadix"]["trait_class"] == "presence"  # carried from the rubric
    assert by["leaf_shape"]["verdict"] == "absent"


def test_parse_traits_rejects_unknown_verdict():
    rubric = [{"key": "k", "trait_class": "color", "expected": "red"}]
    bad = _Resp({"traits": [{"trait_key": "k", "verdict": "banana", "rationale": ""}]})
    try:
        traits.parse_traits(bad, rubric)
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_traits_core.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.traits'`.

- [ ] **Step 3: Implement `app/traits.py`** (mirror `app/judge.py`)

```python
"""VLM trait-checking core: forced-tool per-trait verdicts against a rubric.

Pure except for check_traits, which takes an injected Anthropic-like client (built in
scripts/trait_judge.py). Mirrors app.judge. Verdict vocabulary is exactly the four below."""

from __future__ import annotations

from .judge import JUDGE_MODEL

SCORED_CLASSES = {
    "habit", "organ_shape", "phyllotaxy", "inflorescence", "color", "presence", "proportion",
}
VERDICTS = {"present_correct", "present_wrong", "absent", "not_assessable"}

TRAITS_TOOL = {
    "name": "record_traits",
    "description": "Record, for each listed botanical trait, whether the 3D model satisfies it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "traits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "trait_key": {"type": "string"},
                        "verdict": {
                            "type": "string",
                            "enum": sorted(VERDICTS),
                            "description": "present_correct=trait present & matches expected; "
                            "present_wrong=present but wrong; absent=missing; "
                            "not_assessable=cannot tell from these views",
                        },
                        "rationale": {"type": "string", "description": "One short phrase."},
                    },
                    "required": ["trait_key", "verdict", "rationale"],
                },
            }
        },
        "required": ["traits"],
    },
}


def _img(b64: str) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def build_trait_messages(species: str, prompt: str, sheet_b64: str, traits: list[dict]) -> list[dict]:
    lines = "\n".join(f"- {t['key']} ({t['trait_class']}): expected {t['expected']}" for t in traits)
    text = (
        f"You are checking an AI-generated 3D model of: {species}.\n"
        f"Generation task: {prompt}\n\n"
        "The image is a contact sheet of the model from several angles on a neutral gray "
        "background. For EACH trait below, decide from what is visible whether the model "
        "satisfies it, then call record_traits with one entry per trait (same trait_key). "
        "Use not_assessable only when the views genuinely cannot show the trait.\n\n"
        f"Traits:\n{lines}"
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img(sheet_b64)]}]


def parse_traits(response, traits: list[dict]) -> list[dict]:
    cls_by_key = {t["key"]: t["trait_class"] for t in traits}
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == "record_traits":
            rows = (block.input or {}).get("traits", [])
            out = []
            for r in rows:
                key = r.get("trait_key")
                verdict = r.get("verdict")
                if key not in cls_by_key:
                    continue  # ignore keys not in the rubric
                if verdict not in VERDICTS:
                    raise ValueError(f"invalid verdict: {verdict!r}")
                out.append({"trait_key": key, "trait_class": cls_by_key[key],
                            "verdict": verdict, "rationale": r.get("rationale", "")})
            return out
    raise ValueError("no record_traits tool_use block in response")


def check_traits(client, *, species: str, prompt: str, sheet_b64: str, traits: list[dict]) -> list[dict]:
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1500,
        tools=[TRAITS_TOOL],
        tool_choice={"type": "tool", "name": "record_traits"},
        messages=build_trait_messages(species, prompt, sheet_b64, traits),
    )
    return parse_traits(resp, traits)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_traits_core.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/traits.py tests/test_traits_core.py
git commit -m "feat(mode-c): VLM trait-checking core (forced-tool, injected client)"
```

---

### Task 3: Scoring + per-class κ calibration gate (`app/service.py`)

**Files:**

- Modify: `app/service.py` (add functions; extend the models import with the trait tables)
- Test: `tests/test_trait_scoring.py`

**Interfaces:**

- Consumes: `TraitVerdict`, `TraitScore`, `TraitCalibration`, `calibration.cohens_kappa`.
- Produces:
  - `MODE_C_KAPPA_BAR = 0.6`, `MODE_C_MIN_N = 20`.
  - `recompute_trait_calibration(db, human_labels) -> dict` — `human_labels` is a list of
    `(output_id, trait_key, trait_class, human_verdict)`; pairs with stored `TraitVerdict`s,
    computes per-class κ via `cohens_kappa`, upserts `TraitCalibration`.
  - `accepted_trait_classes(db) -> set[str]`.
  - `recompute_trait_scores(db) -> dict` — per output: `botanical_accuracy = present_correct ÷
assessable`, over verdicts whose `trait_class` is accepted; `not_assessable` excluded; upsert
    `TraitScore`.
  - `trait_leaderboard(db) -> list[dict]` — generator-level mean accuracy (display names,
    Mode-A exclusions reused).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trait_scoring.py
from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import TraitCalibration, TraitScore, TraitVerdict


def setup_module(_m):
    init_db()


def _clear(db):
    db.query(TraitScore).filter(TraitScore.output_id.in_([9001, 9002])).delete(False)
    db.query(TraitVerdict).filter(TraitVerdict.output_id.in_([9001, 9002])).delete(False)
    db.query(TraitCalibration).delete(False)
    db.commit()


def test_scores_use_only_accepted_classes_and_skip_not_assessable():
    with SessionLocal() as db:
        _clear(db)
        # color is accepted; phyllotaxy is NOT calibrated → excluded from the score
        db.add(TraitCalibration(trait_class="color", kappa=0.8, n=30, accepted=True))
        vs = [
            ("color", "present_correct"),
            ("color", "absent"),
            ("color", "not_assessable"),     # excluded
            ("phyllotaxy", "present_correct"),  # excluded (class not accepted)
        ]
        for i, (cls, v) in enumerate(vs):
            db.add(TraitVerdict(output_id=9001, rubric_id=1, trait_key=f"k{i}",
                                trait_class=cls, verdict=v, judge_model="m"))
        db.commit()
        service.recompute_trait_scores(db)
        ts = db.query(TraitScore).filter_by(output_id=9001).one()
        # accepted+assessable color verdicts: 1 correct of 2 → 0.5
        assert ts.n_scored == 2 and ts.botanical_accuracy == 0.5


def test_calibration_gate_threshold():
    with SessionLocal() as db:
        _clear(db)
        labels = [(9002, "k", "color", "present_correct")]
        db.add(TraitVerdict(output_id=9002, rubric_id=1, trait_key="k", trait_class="color",
                            verdict="present_correct", judge_model="m"))
        db.commit()
        # n below MIN_N → not accepted even at perfect agreement
        res = service.recompute_trait_calibration(db, labels)
        cal = db.query(TraitCalibration).filter_by(trait_class="color").one()
        assert cal.accepted is False  # n=1 < MODE_C_MIN_N
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trait_scoring.py -q`
Expected: FAIL — `AttributeError: module 'app.service' has no attribute 'recompute_trait_scores'`.

- [ ] **Step 3: Implement** — extend the models import and append functions to `app/service.py`.

Add `TraitCalibration, TraitScore, TraitVerdict` to the existing `from .models import (...)`
block, and `from . import config, ranking` already exists; add `from .calibration import
cohens_kappa` near the top imports. Then append:

```python
MODE_C_KAPPA_BAR = 0.6
MODE_C_MIN_N = 20


def accepted_trait_classes(db: Session) -> set[str]:
    return {
        c.trait_class
        for c in db.execute(select(TraitCalibration).where(TraitCalibration.accepted.is_(True)))
        .scalars()
    }


def recompute_trait_calibration(db: Session, human_labels) -> dict:
    """human_labels: iterable of (output_id, trait_key, trait_class, human_verdict). Pairs with
    stored TraitVerdicts on (output_id, trait_key); per class, Cohen's kappa of human vs VLM."""
    stored = {
        (v.output_id, v.trait_key): v.verdict
        for v in db.execute(select(TraitVerdict)).scalars()
    }
    by_class: dict[str, tuple[list, list]] = {}
    for oid, key, cls, human in human_labels:
        vlm = stored.get((oid, key))
        if vlm is None:
            continue
        h, m = by_class.setdefault(cls, ([], []))
        h.append(human)
        m.append(vlm)
    written = 0
    for cls, (h, m) in by_class.items():
        k = cohens_kappa(h, m)
        n = len(h)
        accepted = k is not None and k >= MODE_C_KAPPA_BAR and n >= MODE_C_MIN_N
        row = (
            db.execute(select(TraitCalibration).where(TraitCalibration.trait_class == cls))
            .scalars()
            .first()
        )
        if row is None:
            row = TraitCalibration(trait_class=cls)
            db.add(row)
        row.kappa, row.n, row.accepted = k, n, accepted
        written += 1
    db.commit()
    return {"classes": written}


def recompute_trait_scores(db: Session) -> dict:
    accepted = accepted_trait_classes(db)
    by_output: dict[int, list] = {}
    for v in db.execute(select(TraitVerdict)).scalars():
        by_output.setdefault(v.output_id, []).append(v)
    n_out = 0
    for oid, verdicts in by_output.items():
        scored = [v for v in verdicts
                  if v.trait_class in accepted and v.verdict != "not_assessable"]
        n_scored = len(scored)
        correct = sum(1 for v in scored if v.verdict == "present_correct")
        acc = (correct / n_scored) if n_scored else None
        row = (
            db.execute(select(TraitScore).where(TraitScore.output_id == oid)).scalars().first()
        )
        if row is None:
            row = TraitScore(output_id=oid)
            db.add(row)
        row.botanical_accuracy = acc
        row.n_scored = n_scored
        row.n_total = len(verdicts)
        row.judge_model = verdicts[0].judge_model if verdicts else ""
        n_out += 1
    db.commit()
    return {"outputs": n_out}


def trait_leaderboard(db: Session) -> list[dict]:
    """Generator-level mean botanical-accuracy over scored outputs (calibrated classes only)."""
    names = generator_display_names(db)
    excluded = mode_a_excluded_generator_ids(db)
    agg: dict[int, list] = {}
    for ts in db.execute(select(TraitScore)).scalars():
        if ts.botanical_accuracy is None:
            continue
        out = db.get(ModelOutput, ts.output_id)
        if out is None or out.generator_id in excluded or out.is_gold:
            continue
        agg.setdefault(out.generator_id, []).append(ts.botanical_accuracy)
    rows = [
        {
            "generator": names.get(gid, str(gid)),
            "botanical_accuracy": round(sum(v) / len(v), 3),
            "n_outputs": len(v),
        }
        for gid, v in agg.items()
    ]
    rows.sort(key=lambda r: r["botanical_accuracy"], reverse=True)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_trait_scoring.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/service.py tests/test_trait_scoring.py
git commit -m "feat(mode-c): trait scoring + per-class kappa calibration gate"
```

---

### Task 4: Rubric authoring script (`scripts/build_trait_rubrics.py`)

**Files:**

- Create: `scripts/build_trait_rubrics.py`
- Test: `tests/test_build_trait_rubrics.py`

**Interfaces:**

- Produces:
  - `validate_trait(t) -> None` — raises `ValueError` if a trait dict is missing a field, has a
    `trait_class` outside `traits.SCORED_CLASSES`, has empty `citation`, or `source_tier` not in
    `{db, llm}`.
  - `upsert_rubric(db, taxon, task_id, traits) -> TraitRubric` — validates every trait, stores.
  - `main()` — CLI: `--taxa` (default the 6 recon species, mapped to task ids), pluggable
    `fetch_db_traits(taxon)` (structured backbone) + `draft_llm_traits(taxon)` (enrichment;
    real impl calls the Anthropic client, stub-injectable for tests). Each trait stamped with
    `source_tier` + `citation`. Dry-run prints what it would write.

Design note: the structured-DB fetchers (POWO/Wikidata/TRY) and the LLM enrichment are injected
functions so the unit test drives them with stubs; the real implementations live behind
`--live`. The exact DB endpoints are an open question resolved during implementation — keep them
isolated in `fetch_db_traits` so the rest is testable without network.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_trait_rubrics.py
from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.models import TraitRubric


def setup_module(_m):
    init_db()


def test_validate_rejects_uncited_and_bad_class():
    import scripts.build_trait_rubrics as b
    b.validate_trait({"key": "k", "trait_class": "color", "type": "categorical",
                      "expected": "red", "visual": True, "source_tier": "db",
                      "citation": "POWO"})  # ok
    for bad in [
        {"key": "k", "trait_class": "height", "type": "x", "expected": "2m",
         "visual": True, "source_tier": "db", "citation": "POWO"},          # bad class
        {"key": "k", "trait_class": "color", "type": "categorical", "expected": "red",
         "visual": True, "source_tier": "db", "citation": ""},               # empty citation
        {"key": "k", "trait_class": "color", "type": "categorical", "expected": "red",
         "visual": True, "source_tier": "guess", "citation": "x"},           # bad tier
    ]:
        try:
            b.validate_trait(bad)
            assert False, f"expected ValueError for {bad}"
        except ValueError:
            pass


def test_upsert_rubric_persists_validated_traits():
    import scripts.build_trait_rubrics as b
    with SessionLocal() as db:
        db.query(TraitRubric).filter_by(taxon="Test taxon").delete(False)
        db.commit()
        traits = [{"key": "habit", "trait_class": "habit", "type": "categorical",
                   "expected": "herb", "visual": True, "source_tier": "llm",
                   "citation": "Flora 2026"}]
        r = b.upsert_rubric(db, "Test taxon", None, traits)
        assert json.loads(db.get(TraitRubric, r.id).traits_json)[0]["key"] == "habit"
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_build_trait_rubrics.py -q` →
      FAIL (`No module named 'scripts.build_trait_rubrics'`).

- [ ] **Step 3: Implement** `scripts/build_trait_rubrics.py` with the `sys.path` bootstrap used by
      `scripts/judge_vlm.py`. Include `validate_trait`, `upsert_rubric`, injected
      `fetch_db_traits`/`draft_llm_traits` (real impls behind `--live`), and a `main()` that builds
      one rubric per taxon and prints a summary; `--dry-run` writes nothing.

```python
RECON_TAXA = {  # taxon -> task_id placeholder; resolve real ids in main() via Task lookup
    "Solanum lycopersicum": None, "Zea mays": None, "Pinus sylvestris": None,
    "Rosa": None, "Glycine max": None, "Arabidopsis thaliana": None,
}

def validate_trait(t: dict) -> None:
    from app.traits import SCORED_CLASSES
    required = ("key", "trait_class", "type", "expected", "visual", "source_tier", "citation")
    for f in required:
        if f not in t:
            raise ValueError(f"trait missing field {f!r}: {t}")
    if t["trait_class"] not in SCORED_CLASSES:
        raise ValueError(f"trait_class {t['trait_class']!r} not scoreable")
    if t["source_tier"] not in ("db", "llm"):
        raise ValueError(f"bad source_tier {t['source_tier']!r}")
    if not str(t.get("citation", "")).strip():
        raise ValueError("trait has empty citation")

def upsert_rubric(db, taxon, task_id, traits):
    import json as _json
    from app.models import TraitRubric
    for t in traits:
        validate_trait(t)
    row = db.query(TraitRubric).filter_by(taxon=taxon).first() or TraitRubric(taxon=taxon)
    row.task_id = task_id
    row.traits_json = _json.dumps(traits)
    db.add(row)
    db.commit()
    return row
```

- [ ] **Step 4: Run** the test → PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/build_trait_rubrics.py tests/test_build_trait_rubrics.py
git commit -m "feat(mode-c): trait-rubric authoring + provenance validation"
```

---

### Task 5: Resumable trait-judge batch driver (`scripts/trait_judge.py`)

**Files:**

- Create: `scripts/trait_judge.py`
- Test: `tests/test_trait_judge.py`

**Interfaces:**

- Consumes: `app.traits.check_traits`, `judge_render.render_contact_sheets`, `TraitRubric`,
  `TraitVerdict`, `app.sourcing` exclusions.
- Produces:
  - `enumerate_work(db, task_ids) -> list[dict]` — one item per (output, rubric) for non-gold,
    non-excluded outputs of tasks that have a rubric.
  - `existing_keys(db) -> set` — `(output_id, trait_key, judge_model)` already stored.
  - `run_batch(db, *, check_fn, sheet_b64, work=None, max_outputs=None) -> dict` — for each
    output, call `check_fn(species, prompt, sheet_b64(output_id), traits)`, persist a
    `TraitVerdict` per returned trait (skip existing keys); count written/skipped/errors.
  - `main()` — `--tasks`, `--dry-run` (count, no API/browser import), `--max`.

- [ ] **Step 1: Write the failing test** (stub check_fn + sheet provider; no network/browser)

```python
# tests/test_trait_judge.py
from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.models import (Category, Generator, ModelOutput, Task, TraitRubric, TraitVerdict)


def setup_module(_m):
    init_db()


def _seed(db):
    db.query(TraitVerdict).filter(TraitVerdict.judge_model == "stub").delete(False)
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("tj/%")).delete(False)
    db.query(TraitRubric).filter_by(taxon="TJ").delete(False)
    db.query(Task).filter_by(title="tj-task").delete(False)
    db.query(Generator).filter(Generator.slug.like("tj-%")).delete(False)
    db.query(Category).filter_by(slug="tj-cat").delete(False)
    db.commit()
    cat = Category(slug="tj-cat", name="C"); db.add(cat); db.flush()
    task = Task(category_id=cat.id, title="tj-task", prompt="p"); db.add(task); db.flush()
    db.add(TraitRubric(taxon="TJ", task_id=task.id, traits_json=json.dumps(
        [{"key": "habit", "trait_class": "habit", "expected": "herb"}])))
    g = Generator(slug="tj-g", name="G"); db.add(g); db.flush()
    o = ModelOutput(task_id=task.id, generator_id=g.id, asset_path="tj/a.glb",
                    asset_format="glb", source="api:fal:trellis"); db.add(o); db.flush()
    db.commit()
    return task, o


def test_run_batch_writes_verdicts_and_is_resumable():
    import scripts.trait_judge as tj
    with SessionLocal() as db:
        task, o = _seed(db)
        work = tj.enumerate_work(db, [task.id])
        assert len(work) == 1

        def check_fn(species, prompt, sheet_b64, traits):
            return [{"trait_key": "habit", "trait_class": "habit",
                     "verdict": "present_correct", "rationale": "ok"}]

        res = tj.run_batch(db, check_fn=check_fn, sheet_b64=lambda oid: "x", work=work)
        assert res["written"] == 1
        assert db.query(TraitVerdict).filter_by(output_id=o.id, judge_model="stub").count() == 1
        # resumable: second run skips
        res2 = tj.run_batch(db, check_fn=check_fn, sheet_b64=lambda oid: "x", work=work)
        assert res2["written"] == 0 and res2["skipped"] >= 1
```

Note: `run_batch` stamps `judge_model="stub"` only when the injected `check_fn` is the test's;
in production it uses `app.judge.JUDGE_MODEL`. Implement by reading the model off a `judge_model`
kwarg defaulting to `JUDGE_MODEL`; the test passes `judge_model="stub"`.

- [ ] **Step 2: Run** → FAIL (`No module named 'scripts.trait_judge'`).

- [ ] **Step 3: Implement** `scripts/trait_judge.py` mirroring `scripts/judge_vlm.py`:
      `sys.path` bootstrap; `enumerate_work` (join outputs↔rubric, exclude gold + reference-scan +
      untextured); `existing_keys`; `run_batch(work=None, judge_model=JUDGE_MODEL, max_outputs=None)`
      persisting one `TraitVerdict` per returned trait, per-output commit, skip on existing key,
      count errors; `main()` with `--tasks`/`--dry-run`/`--max` where `--dry-run` computes the
      uncovered count and exits BEFORE importing anthropic or the browser capture (a real
      `_real_sheet_b64_factory` like judge_vlm's, reusing `render_contact_sheets` multi4).

- [ ] **Step 4: Run** → PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/trait_judge.py tests/test_trait_judge.py
git commit -m "feat(mode-c): resumable trait-judge batch driver (dry-run + max)"
```

---

### Task 6: Surfacing — scorecard, board, coverage/difficulty columns, exports

**Files:**

- Modify: `app/main.py` (routes), `app/templates/coverage.html` (Mode-C column),
  `app/templates/difficulty.html` (Mode-C column)
- Create: `app/templates/trait_scorecard.html`
- Test: `tests/test_trait_pages.py`

**Interfaces:**

- Consumes: `service.trait_leaderboard`, `TraitRubric`, `TraitVerdict`, `TraitScore`.
- Produces routes: `GET /trait/{output_id}` (scorecard), `GET /api/traits.json`,
  `GET /api/trait_scores.json`; a Mode-C board section (on `/benchmark` or a new `/traits` page —
  decide during implementation; default: a section in the existing benchmark route context).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trait_pages.py
from __future__ import annotations

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_trait_json_endpoints_shape():
    r = client.get("/api/trait_scores.json")
    assert r.status_code == 200
    data = r.json()
    assert "generators" in data and isinstance(data["generators"], list)
    r2 = client.get("/api/traits.json")
    assert r2.status_code == 200
    assert "rubrics" in r2.json()


def test_trait_scorecard_route_handles_missing_output():
    # unknown output → 404, not 500
    assert client.get("/trait/99999999").status_code == 404
```

- [ ] **Step 2: Run** → FAIL (routes missing → 404 for the JSON endpoints / wrong shape).

- [ ] **Step 3: Implement** the routes in `app/main.py`:
  - `/api/trait_scores.json` → `{"generators": service.trait_leaderboard(db), "outputs": [...]}`.
  - `/api/traits.json` → `{"rubrics": [ {taxon, task_id, traits:[...]} ]}` from `TraitRubric`.
  - `/trait/{output_id}` → 404 if no output; else render `trait_scorecard.html` with the
    output's `TraitVerdict` rows joined to the rubric (expected + citation + verdict +
    calibrated-class marker) and its `TraitScore`.
  - Add a Mode-C column to the `/coverage` task table (rubric present? n traits? mean accuracy?)
    by extending `service.coverage_summary` task rows with `has_rubric` + `mode_c_accuracy`, and
    a Mode-C per-tier number to the difficulty context. (Extend the existing tests for those
    aggregates accordingly.)
  - Add a "Mode-C — experimental" banner wherever scores show until
    `service.accepted_trait_classes(db)` is non-empty.

- [ ] **Step 4: Run** `tests/test_trait_pages.py` (and the coverage/difficulty tests) → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/templates/ app/service.py tests/test_trait_pages.py
git commit -m "feat(mode-c): scorecard + board + coverage/difficulty columns + JSON export"
```

---

### Task 7: Live real-execution check + calibration dry-run (no merge-blocking spend)

**Files:**

- Create: `scripts/trait_judge.py` already exists; this task adds a documented runbook in
  `docs/superpowers/specs/2026-06-29-mode-c-trait-gt-design.md` "Rollout" only if needed.
- Test: none new (this is the manual real-execution gate).

**Steps (operator-run, study env; NOT pytest):**

- [ ] **Step 1: Author rubrics** for the 6 recon species:
      `BIO3D_DATABASE_URL=<study> BIO3D_DATA_DIR=<mvp> .venv/bin/python scripts/build_trait_rubrics.py --live`
- [ ] **Step 2: Dry-run the trait-judge** to get the call count before any spend:
      `... scripts/trait_judge.py --dry-run` → record N calls; confirm with the user before running.
- [ ] **Step 3: Real-execution check** — run `trait_judge.py --max 3` on 3 real outputs and
      eyeball the persisted verdicts + rationales for sanity (does it actually detect a spadix?).
- [ ] **Step 4:** Only after the user approves the count, run the full pass + `recompute_trait_scores`.
- [ ] **Step 5:** Snapshot the study DB before/after (per the incident rule); verify `/trait/<id>`
      and the JSON exports live; leave Mode-C "experimental" until a human-label calibration pass
      produces an accepted class.

---

## Self-Review

- **Spec coverage:** data model (T1), trait-check core (T2), scoring+κ gate (T3), rubric
  authoring+provenance (T4), resumable judge (T5), surfacing+exports (T6), live check+calibration
  (T7) — every spec section maps to a task.
- **Placeholder scan:** the external DB endpoints in T4 and the board-location choice in T6 are
  explicitly flagged as implementation-time decisions isolated behind injected functions /
  defaults, not silent gaps; all code steps contain real code.
- **Type consistency:** `verdict` vocabulary, `trait_class` set, `botanical_accuracy` semantics,
  and the κ-gate (`>=0.6`, `n>=20`) are identical across T1/T2/T3/T6 and the Global Constraints.
- **Reuse correctness:** `judge.JUDGE_MODEL`, `judge_render.render_contact_sheets`,
  `calibration.cohens_kappa`, `service.generator_display_names`/`mode_a_excluded_generator_ids`
  are all verified-present interfaces (grounded against live code 2026-06-29).

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-06-29-mode-c-trait-gt.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh implementer subagent per task, task review between
   tasks, broad review at the end.
2. **Inline Execution** — execute tasks in this session via executing-plans, batch with checkpoints.
