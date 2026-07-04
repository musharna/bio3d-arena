# Semantic-Admissibility Predicate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a VLM-based semantic-admissibility predicate that rejects cardinality/identity failures (multiple plants, a detached organ, a non-plant, the wrong species) which structural geometry cannot see — shipping advisory-by-default and promotable to gating after a zero-false-positive acceptance run.

**Architecture:** A new `app/semantic.py` clones the `app/completeness.py` VLM-judge shape (one forced-tool call over a turntable contact sheet) and reuses `app/structural.upsert_verdict` for persistence — writing `Admissibility` rows under `predicate="semantic"` with **no schema change**. A `SemanticPredicate` plugs into the existing composer; a config mode (`off/advisory/gate`) decides whether rejects are dormant, surfaced to the ⚑ review queue as non-hiding flags, or auto-excluded from the vote pool. The pool gate call site (`main.py`) is unchanged — `non_admitted_output_ids` becomes mode-aware for its default rubric.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Anthropic SDK (forced tool-use, `JUDGE_MODEL="claude-sonnet-4-6"`), trimesh (existing), Playwright contact-sheet capture (existing, injected), pytest.

## Global Constraints

- Test runner: `.venv/bin/pytest`, `BIO3D_DATABASE_URL` **UNSET**. NEVER run pytest or serve with `BIO3D_DATABASE_URL=study`. NEVER score/serve the real study DB `data/study/arena-study.db` — always a COPY.
- Baseline before this work: **679 passed / 8 skipped**. Each task keeps the full suite green.
- Precision-first: **`uncertain` (and any unrecognized code) → admit**; reject only on confident inadmissible codes. **Zero false positives on good outputs is a merge-blocking acceptance criterion** (Task 5).
- Default `BIO3D_SEMANTIC_ADMISSIBILITY_MODE=advisory`; promote to `gate` only after the Task 5 acceptance run passes zero-FP.
- Reuse (do not reinvent): `app/completeness.py` VLM shape (`_img_block`/`_build_messages`/`_parse`/`score_*`, injected `sheet_for`, `JUDGE_MODEL`); `app/structural.upsert_verdict` (generic over predicate name); `app/flags.record_flag(db, output_id, session_id, reason, threshold)`; `app/sourcing.is_reference_scan(source)` / `is_untextured_output(output)`; `app/judge_render.py` (`contact_sheet_path`/`render_contact_sheets`/`CONDITIONS`); `scripts/judge_capture.browser_capture_multi_factory`; `scripts/score_completeness.py` as the backfill template.
- Contact-sheet **condition is `"turntable"`** (same as completeness) so cached sheets from prior completeness runs are reused.
- No new dependency. New `Admissibility` rows only (table + `predicate` column already exist). `SemanticPredicate` reads precomputed rows — never calls the VLM at `/api/next` request time.
- Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012PR54CiPisFk9i8L5MUTka
  ```

---

## File Structure

- **Create `app/semantic.py`** — the predicate: verdict codes + tool + prompt + `score_semantic` (Task 1); `enumerate_semantic_work` + `evaluate_outputs` + `SemanticPredicate` (Task 2).
- **Modify `app/config.py`** — add `SEMANTIC_ADMISSIBILITY_MODE` (Task 1).
- **Modify `app/admissibility.py`** — register `SemanticPredicate`, add mode-aware `_effective_rubric` (Task 3).
- **Create `scripts/score_semantic.py`** — backfill driver, cloned from `score_completeness.py` (Task 4).
- **Create `docs/results/2026-07-03-semantic-admissibility-results.md`** — acceptance-run report (Task 5).
- **Create tests**: `tests/test_semantic_scorer.py` (Task 1), `tests/test_semantic_batch.py` (Task 2), `tests/test_semantic_pool.py` (Task 3), `tests/test_score_semantic_script.py` (Task 4).

Circular-import note: `app/semantic.py` imports `Verdict` from `app/admissibility.py` at module level (same as `structural.py` does). `admissibility._registry()` imports `SemanticPredicate` **function-locally** (same as `StructuralPredicate`). `structural.upsert_verdict` is imported **function-locally** inside `evaluate_outputs` so Task 1's pure core stays numpy-free and light.

---

### Task 1: Config flag + semantic VLM core (tool, prompt, verdict mapping, scorer)

**Files:**

- Modify: `app/config.py` (after the "Bad-output handling" block, ~line 51)
- Create: `app/semantic.py`
- Test: `tests/test_semantic_scorer.py`

**Interfaces:**

- Consumes: `app.admissibility.Verdict`, `app.judge.JUDGE_MODEL`.
- Produces:
  - `config.SEMANTIC_ADMISSIBILITY_MODE: str` (`"off"|"advisory"|"gate"`, default `"advisory"`).
  - `semantic.VERSION = "semantic-v1"`, `ADMIT_CODES`, `REJECT_CODES`, `SEMANTIC_FLAG_SESSION`, `ADVISORY_NO_HIDE_THRESHOLD`, `SEMANTIC_TOOL`.
  - `verdict_from_code(code: str, note: str = "") -> Verdict`
  - `score_semantic(client, sheet_png: bytes, *, taxon: str | None) -> dict` returning `{"verdict": str, "note": str}`
  - `_build_messages(png: bytes, taxon: str | None) -> list[dict]` (taxon-gated prompt)

- [ ] **Step 1: Add the config flag**

In `app/config.py`, immediately after the `FLAG_HIDE_THRESHOLD` line (~line 51), add:

```python
# --- Semantic-admissibility predicate (VLM cardinality+identity) ---
# off: dormant (not in the rubric, no advisory flags). advisory: surfaces confident rejects to
# the ⚑ review queue as non-hiding flags but does NOT auto-exclude. gate: auto-excludes rejects
# from the vote pool. Promote advisory -> gate only after a zero-FP-on-good acceptance run.
SEMANTIC_ADMISSIBILITY_MODE = os.environ.get(
    "BIO3D_SEMANTIC_ADMISSIBILITY_MODE", "advisory"
).lower()
```

- [ ] **Step 2: Write the failing test** (`tests/test_semantic_scorer.py`)

```python
# tests/test_semantic_scorer.py
import pytest

from app.semantic import (
    REJECT_CODES,
    _build_messages,
    score_semantic,
    verdict_from_code,
)


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


def test_reject_codes_map_to_non_admit_with_reason():
    for code in ["multiple", "sub_part", "not_a_plant", "wrong_species"]:
        v = verdict_from_code(code, "because")
        assert v.admit is False
        assert v.reason == code
        assert v.detail["code"] == code and v.detail["note"] == "because"


def test_ok_and_uncertain_admit():
    assert verdict_from_code("ok").admit is True
    assert verdict_from_code("uncertain").admit is True


def test_unrecognized_code_admits_precision_first():
    v = verdict_from_code("banana")
    assert v.admit is True and v.reason == ""


def test_reject_codes_constant_is_the_four_semantic_failures():
    assert REJECT_CODES == {"multiple", "sub_part", "not_a_plant", "wrong_species"}


def test_build_messages_taxon_present_includes_wrong_species_clause():
    msgs = _build_messages(b"\x89PNG", taxon="tomato")
    text = msgs[0]["content"][0]["text"]
    assert "tomato" in text and "wrong_species" in text


def test_build_messages_taxon_none_omits_wrong_species():
    msgs = _build_messages(b"\x89PNG", taxon=None)
    text = msgs[0]["content"][0]["text"]
    assert "wrong_species" not in text
    # still has the taxon-agnostic reject codes + an image block
    assert "multiple" in text and "not_a_plant" in text
    assert msgs[0]["content"][1]["type"] == "image"


def test_score_semantic_parses_tool_block():
    client = _FakeClient(_Resp([_Block("record_admissibility", {"verdict": "multiple", "note": "two plants"})]))
    out = score_semantic(client, b"\x89PNG", taxon="maize")
    assert out == {"verdict": "multiple", "note": "two plants"}


def test_score_semantic_raises_without_tool_block():
    client = _FakeClient(_Resp([]))
    with pytest.raises(ValueError):
        score_semantic(client, b"\x89PNG", taxon=None)
```

- [ ] **Step 3: Run it, verify it fails**

Run: `.venv/bin/pytest tests/test_semantic_scorer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.semantic'`.

- [ ] **Step 4: Write `app/semantic.py` (core only)**

```python
# app/semantic.py
"""Semantic-admissibility predicate: one VLM tool-use call over an output's turntable contact
sheet judging whether it is a single, whole, valid plant specimen. Rejects cardinality/identity
failures (multiple plants, a detached organ, a non-plant, the wrong species) that structural
geometry cannot see. Precision-first: uncertain (and any unmapped code) -> admit. Clones
app.completeness's VLM-judge shape; persistence reuses app.structural.upsert_verdict
(predicate='semantic', no schema change)."""

from __future__ import annotations

import base64

from .admissibility import Verdict
from .judge import JUDGE_MODEL

VERSION = "semantic-v1"

ADMIT_CODES = {"ok", "uncertain"}
REJECT_CODES = {"multiple", "sub_part", "not_a_plant", "wrong_species"}

# Advisory flags use one synthetic session id (record_flag is idempotent per (output, session_id)
# and requires a non-null id) and a sentinel threshold so an advisory flag NEVER auto-hides the
# output — advisory surfaces to the review queue; it does not remove from the pool (that is gating).
SEMANTIC_FLAG_SESSION = "semantic-v1"
ADVISORY_NO_HIDE_THRESHOLD = 10**9

SEMANTIC_TOOL = {
    "name": "record_admissibility",
    "description": "Judge whether the rendered model is a single, whole, valid plant specimen.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["ok", "multiple", "sub_part", "not_a_plant", "wrong_species", "uncertain"],
            },
            "note": {"type": "string"},
        },
        "required": ["verdict", "note"],
    },
}


def verdict_from_code(code: str, note: str = "") -> Verdict:
    """Map a VLM verdict code to an admissibility Verdict. admit iff code not in REJECT_CODES —
    so ok, uncertain, AND any unrecognized code admit (precision-first: never reject on a code we
    cannot map)."""
    admit = code not in REJECT_CODES
    reason = "" if admit else code
    return Verdict(admit, reason, {"code": code, "note": note})


def _img_block(png: bytes) -> dict:
    b64 = base64.b64encode(png).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


def _build_messages(png: bytes, taxon: str | None) -> list[dict]:
    of_taxon = f" of {taxon}" if taxon else ""
    wrong_species = f"`wrong_species` (a plant, but clearly not a {taxon}), " if taxon else ""
    text = (
        f"This is a contact sheet of a generated 3D model{of_taxon}, rendered from several angles "
        "on a neutral gray background. Judge whether it is a SINGLE, WHOLE, VALID plant specimen. "
        "Reject as: `multiple` (more than one distinct plant, or a scene/cluster), "
        "`sub_part` (only a detached organ — a single fruit, leaf, or flower — not a whole plant), "
        "`not_a_plant` (not a recognizable plant at all — a blob or non-plant object), "
        f"{wrong_species}"
        "Otherwise answer `ok`. If you genuinely cannot tell, answer `uncertain`. "
        "Reject ONLY when clearly inadmissible; when in doubt, prefer `ok` or `uncertain`. "
        "Then call record_admissibility."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img_block(png)]}]


def _parse(response) -> dict:
    for block in getattr(response, "content", []):
        if (
            getattr(block, "type", "") == "tool_use"
            and getattr(block, "name", "") == "record_admissibility"
        ):
            inp = block.input or {}
            return {"verdict": inp.get("verdict", "uncertain"), "note": inp.get("note", "")}
    raise ValueError("no record_admissibility tool_use block in response")


def score_semantic(client, sheet_png: bytes, *, taxon: str | None) -> dict:
    """One VLM call over the contact sheet; returns {'verdict': str, 'note': str}."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=300,
        tools=[SEMANTIC_TOOL],
        tool_choice={"type": "tool", "name": "record_admissibility"},
        messages=_build_messages(sheet_png, taxon),
    )
    return _parse(resp)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_semantic_scorer.py -q`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/semantic.py tests/test_semantic_scorer.py
git commit -m "feat(semantic): config mode + VLM core (tool, taxon-gated prompt, verdict mapping)"
```

---

### Task 2: Enumerate + persistence/advisory + SemanticPredicate

**Files:**

- Modify: `app/semantic.py` (append)
- Test: `tests/test_semantic_batch.py`

**Interfaces:**

- Consumes: `verdict_from_code`, `score_semantic`, `VERSION`, `SEMANTIC_FLAG_SESSION`, `ADVISORY_NO_HIDE_THRESHOLD` (Task 1); `app.structural.upsert_verdict`; `app.flags.record_flag`; `app.sourcing.is_reference_scan`/`is_untextured_output`; `app.models.Admissibility`/`ModelOutput`/`TraitRubric`.
- Produces:
  - `enumerate_semantic_work(db) -> list[dict]` — rows `{"output_id": int, "taxon": str | None}`
  - `evaluate_outputs(db, work, *, client, sheet_for, emit_flags: bool) -> dict` — `{"scored", "errors", "flagged", "failures"}`
  - `SemanticPredicate` (`name="semantic"`, `version=VERSION`, `rejected_output_ids(db) -> set[int]`)

- [ ] **Step 1: Write the failing test** (`tests/test_semantic_batch.py`)

```python
# tests/test_semantic_batch.py
from app.database import SessionLocal, init_db
from app.models import (
    Admissibility,
    Category,
    Generator,
    ModelOutput,
    OutputFlag,
    Task,
    TraitRubric,
)
from app.semantic import (
    SemanticPredicate,
    VERSION,
    enumerate_semantic_work,
    evaluate_outputs,
)


def setup_module(_m):
    init_db()


def _cat_gen(db):
    cat = db.query(Category).filter_by(slug="sem-batch").one_or_none()
    if cat is None:
        cat = Category(slug="sem-batch", name="Solanum lycopersicum")
        db.add(cat)
        db.flush()
    gen = db.query(Generator).filter_by(slug="sem-gen").one_or_none()
    if gen is None:
        gen = Generator(slug="sem-gen", name="sem-gen", kind="model", paradigm="")
        db.add(gen)
        db.flush()
    return cat, gen


def _output(db, cat, gen, *, taxon=None, is_gold=False, source="bio3d-arena"):
    task = Task(category_id=cat.id, title="t", prompt="p")
    db.add(task)
    db.flush()
    if taxon is not None:
        db.add(TraitRubric(task_id=task.id, taxon=taxon, traits_json="[]"))
    out = ModelOutput(
        task_id=task.id, generator_id=gen.id, asset_path="p.glb", is_gold=is_gold, source=source
    )
    db.add(out)
    db.flush()
    return out.id


class _FakeClient:
    """client.messages.create -> a record_admissibility tool_use with a fixed verdict."""

    def __init__(self, verdict):
        self._verdict = verdict
        self.messages = self

    def create(self, **kw):
        verdict = self._verdict

        class B:
            type = "tool_use"
            name = "record_admissibility"
            input = {"verdict": verdict, "note": "n"}

        class R:
            content = [B()]

        return R()


def test_enumerate_includes_taxonless_and_excludes_ineligible():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        taxonless = _output(db, cat, gen, taxon=None)
        with_taxon = _output(db, cat, gen, taxon="Solanum lycopersicum")
        gold = _output(db, cat, gen, is_gold=True)
        db.commit()
        work = enumerate_semantic_work(db)
        by_id = {w["output_id"]: w["taxon"] for w in work}
        assert taxonless in by_id and by_id[taxonless] is None
        assert with_taxon in by_id and by_id[with_taxon] == "Solanum lycopersicum"
        assert gold not in by_id


def test_enumerate_skips_current_version_verdicts():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid = _output(db, cat, gen, taxon=None)
        db.add(Admissibility(output_id=oid, predicate="semantic", admit=True, version=VERSION))
        db.commit()
        assert oid not in {w["output_id"] for w in enumerate_semantic_work(db)}


def test_evaluate_upserts_reject_verdict_and_fail_loud():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid_ok = _output(db, cat, gen, taxon="Solanum lycopersicum")
        oid_raise = _output(db, cat, gen, taxon=None)
        db.commit()

        def sheet_for(oid):
            if oid == oid_raise:
                raise RuntimeError("render failed")
            return b"\x89PNG"

        work = [{"output_id": oid_ok, "taxon": "Solanum lycopersicum"},
                {"output_id": oid_raise, "taxon": None}]
        summary = evaluate_outputs(
            db, work, client=_FakeClient("multiple"), sheet_for=sheet_for, emit_flags=False
        )
        db.commit()
        assert summary["scored"] == 1 and summary["errors"] == 1
        row = db.query(Admissibility).filter_by(output_id=oid_ok, predicate="semantic").one()
        assert row.admit is False and row.reason == "multiple" and row.version == VERSION


def test_advisory_flag_is_non_hiding_and_only_on_reject():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid_bad = _output(db, cat, gen, taxon=None)
        oid_good = _output(db, cat, gen, taxon=None)
        db.commit()

        # reject + emit_flags -> exactly one flag, output NOT hidden
        evaluate_outputs(
            db, [{"output_id": oid_bad, "taxon": None}],
            client=_FakeClient("sub_part"), sheet_for=lambda o: b"\x89PNG", emit_flags=True,
        )
        db.commit()
        flags_bad = db.query(OutputFlag).filter_by(output_id=oid_bad).all()
        assert len(flags_bad) == 1 and flags_bad[0].reason == "sub_part"
        assert db.get(ModelOutput, oid_bad).hidden_at is None

        # ok verdict + emit_flags -> no flag
        evaluate_outputs(
            db, [{"output_id": oid_good, "taxon": None}],
            client=_FakeClient("ok"), sheet_for=lambda o: b"\x89PNG", emit_flags=True,
        )
        db.commit()
        assert db.query(OutputFlag).filter_by(output_id=oid_good).count() == 0


def test_emit_flags_false_upserts_but_does_not_flag():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid = _output(db, cat, gen, taxon=None)
        db.commit()
        evaluate_outputs(
            db, [{"output_id": oid, "taxon": None}],
            client=_FakeClient("not_a_plant"), sheet_for=lambda o: b"\x89PNG", emit_flags=False,
        )
        db.commit()
        assert db.query(Admissibility).filter_by(output_id=oid, predicate="semantic").one().admit is False
        assert db.query(OutputFlag).filter_by(output_id=oid).count() == 0


def test_semantic_predicate_reads_rejects():
    with SessionLocal() as db:
        cat, gen = _cat_gen(db)
        oid = _output(db, cat, gen, taxon=None)
        db.add(Admissibility(output_id=oid, predicate="semantic", admit=False, reason="multiple", version=VERSION))
        db.commit()
        assert oid in SemanticPredicate().rejected_output_ids(db)
```

- [ ] **Step 2: Run it, verify it fails**

Run: `.venv/bin/pytest tests/test_semantic_batch.py -q`
Expected: FAIL — `ImportError: cannot import name 'enumerate_semantic_work'`.

- [ ] **Step 3: Append to `app/semantic.py`**

Add these imports to the top of `app/semantic.py` (after the existing imports):

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import flags
from .models import Admissibility, ModelOutput, TraitRubric
from .sourcing import is_reference_scan, is_untextured_output
```

(`app/semantic.py` never reads `config` — the mode logic lives in `app/admissibility.py` and `scripts/score_semantic.py`, so do NOT import config here.) Then append:

```python
def enumerate_semantic_work(db: Session) -> list[dict]:
    """One {'output_id', 'taxon'} per eligible output lacking a current-VERSION semantic verdict.
    Eligible = non-gold, non-reference-scan, non-untextured (structural's breadth — NOT gated on a
    taxon inventory, so this reaches the outputs completeness never scored). taxon = the output's
    task's TraitRubric.taxon if a rubric exists, else None (the taxon-agnostic checks still run;
    only wrong_species needs a taxon)."""
    have = {
        oid
        for (oid,) in db.execute(
            select(Admissibility.output_id).where(
                Admissibility.predicate == "semantic", Admissibility.version == VERSION
            )
        ).all()
    }
    taxon_by_task = {
        tid: taxon
        for (tid, taxon) in db.execute(select(TraitRubric.task_id, TraitRubric.taxon)).all()
    }
    work: list[dict] = []
    outs = db.execute(select(ModelOutput).where(ModelOutput.is_gold.is_(False))).scalars().all()
    for out in outs:
        if out.id in have:
            continue
        if is_reference_scan(out.source) or is_untextured_output(out):
            continue
        work.append({"output_id": out.id, "taxon": taxon_by_task.get(out.task_id)})
    return work


def evaluate_outputs(db: Session, work, *, client, sheet_for, emit_flags: bool) -> dict:
    """Score each work row and upsert its semantic verdict (persistence is UNCONDITIONAL — the
    acceptance run reads these rows regardless of mode). Fail-loud per output: an unreadable sheet
    or a VLM error is recorded and the batch continues. If emit_flags AND the verdict rejects, also
    record a NON-HIDING advisory OutputFlag (sentinel threshold) so a human sees it in the ⚑ queue.
    Caller commits."""
    from .structural import upsert_verdict  # function-local: avoids importing numpy in Task-1 core

    scored = errors = flagged = 0
    failures: list[dict] = []
    seen: set[int] = set()
    for item in work:
        oid = item["output_id"]
        if oid in seen:
            continue
        seen.add(oid)
        try:
            png = sheet_for(oid)
            result = score_semantic(client, png, taxon=item.get("taxon"))
            verdict = verdict_from_code(result["verdict"], result.get("note", ""))
            upsert_verdict(db, oid, "semantic", verdict, VERSION)
            scored += 1
            if emit_flags and not verdict.admit:
                flags.record_flag(
                    db, oid, SEMANTIC_FLAG_SESSION, verdict.reason, ADVISORY_NO_HIDE_THRESHOLD
                )
                flagged += 1
        except Exception as e:  # noqa: BLE001 — fail-loud per output, never abort the batch
            errors += 1
            failures.append({"output_id": oid, "error": repr(e)})
    return {"scored": scored, "errors": errors, "flagged": flagged, "failures": failures}


class SemanticPredicate:
    name = "semantic"
    version = VERSION

    def rejected_output_ids(self, db: Session) -> set[int]:
        return {
            oid
            for (oid,) in db.execute(
                select(Admissibility.output_id).where(
                    Admissibility.predicate == "semantic", Admissibility.admit.is_(False)
                )
            ).all()
        }
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_semantic_batch.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/semantic.py tests/test_semantic_batch.py
git commit -m "feat(semantic): enumerate + persistence/advisory-flag + SemanticPredicate"
```

---

### Task 3: Composer registration + mode-aware effective rubric + pool parity

**Files:**

- Modify: `app/admissibility.py` (`_registry`, add `_effective_rubric`, `non_admitted_output_ids`)
- Test: `tests/test_semantic_pool.py`

**Interfaces:**

- Consumes: `app.semantic.SemanticPredicate` (function-local import); `config.SEMANTIC_ADMISSIBILITY_MODE`.
- Produces: `admissibility._effective_rubric() -> list[str]`; `non_admitted_output_ids(db, rubric=None)` now includes `"semantic"` iff `rubric is None and config.SEMANTIC_ADMISSIBILITY_MODE == "gate"`. `DEFAULT_RUBRIC` stays `["structural", "completeness"]` (unchanged).
- **No change to `app/main.py`** — `main.py:277` calls `non_admitted_output_ids(db)` (rubric=None), which now picks up semantic in gate mode automatically.

- [ ] **Step 1: Write the failing test** (`tests/test_semantic_pool.py`)

```python
# tests/test_semantic_pool.py
from __future__ import annotations

import uuid

from app import admissibility, config
from app.database import SessionLocal, init_db
from app.models import Admissibility, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _rejected_semantic_output(db):
    g = Generator(slug=f"sp-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"sp-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
    db.add(o)
    db.flush()
    db.add(Admissibility(output_id=o.id, predicate="semantic", admit=False, reason="multiple", version="semantic-v1"))
    db.commit()
    return o.id


def test_default_rubric_unchanged():
    assert admissibility.DEFAULT_RUBRIC == ["structural", "completeness"]


def test_direct_semantic_rubric_rejects_regardless_of_mode(monkeypatch):
    with SessionLocal() as db:
        oid = _rejected_semantic_output(db)
        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "off")
        assert oid in admissibility.non_admitted_output_ids(db, rubric=["semantic"])


def test_default_gate_includes_semantic_only_in_gate_mode(monkeypatch):
    with SessionLocal() as db:
        oid = _rejected_semantic_output(db)

        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "gate")
        assert oid in admissibility.non_admitted_output_ids(db)  # rubric=None

        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "advisory")
        assert oid not in admissibility.non_admitted_output_ids(db)

        monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "off")
        assert oid not in admissibility.non_admitted_output_ids(db)


def test_effective_rubric_appends_semantic_in_gate(monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "gate")
    assert admissibility._effective_rubric() == ["structural", "completeness", "semantic"]
    monkeypatch.setattr(config, "SEMANTIC_ADMISSIBILITY_MODE", "advisory")
    assert admissibility._effective_rubric() == ["structural", "completeness"]
```

- [ ] **Step 2: Run it, verify it fails**

Run: `.venv/bin/pytest tests/test_semantic_pool.py -q`
Expected: FAIL — `AttributeError: module 'app.admissibility' has no attribute '_effective_rubric'` (and the gate-mode assertion fails, since semantic is not yet registered).

- [ ] **Step 3: Edit `app/admissibility.py`**

Replace the `_registry` body to add semantic:

```python
def _registry() -> dict[str, Predicate]:
    # Function-local imports: structural.py and semantic.py import Verdict from this module, so a
    # module-level import here would be a real circular import. Direct (unguarded) — a genuine
    # ImportError must fail loud, not degrade to "predicate absent".
    from .semantic import SemanticPredicate
    from .structural import StructuralPredicate

    return {
        "completeness": CompletenessPredicate(),
        "structural": StructuralPredicate(),
        "semantic": SemanticPredicate(),
    }
```

Add `_effective_rubric` directly below `DEFAULT_RUBRIC`:

```python
def _effective_rubric() -> list[str]:
    """The default rubric, plus 'semantic' iff the semantic predicate is in gate mode. DEFAULT_RUBRIC
    stays static (advisory/off never auto-exclude); the composer only reaches the semantic predicate
    for the default (rubric=None) call, which is the live pool gate. Explicit rubric= calls are used
    verbatim, unaffected by the mode."""
    rubric = list(DEFAULT_RUBRIC)
    if config.SEMANTIC_ADMISSIBILITY_MODE == "gate":
        rubric.append("semantic")
    return rubric
```

Change `non_admitted_output_ids` to use it when `rubric is None`:

```python
def non_admitted_output_ids(db: Session, rubric: list[str] | None = None) -> set[int]:
    """Union of rejected ids across the rubric's predicates. rubric=None -> the mode-aware effective
    rubric (DEFAULT_RUBRIC + semantic-if-gate). Unknown predicate name -> KeyError (fail-loud)."""
    reg = _registry()
    names = _effective_rubric() if rubric is None else rubric
    out: set[int] = set()
    for name in names:
        out |= reg[name].rejected_output_ids(db)
    return out
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_semantic_pool.py tests/test_admissibility.py -q`
Expected: PASS (existing `test_admissibility.py` still green — `DEFAULT_RUBRIC` unchanged, explicit-rubric calls unaffected).

- [ ] **Step 5: Full-suite regression check**

Run: `.venv/bin/pytest -q`
Expected: 679+ passed (new tests added), 8 skipped, 0 failed. In particular `tests/test_pool_autogate.py` stays green (the default pool gate is unchanged in the default `advisory` mode).

- [ ] **Step 6: Commit**

```bash
git add app/admissibility.py tests/test_semantic_pool.py
git commit -m "feat(semantic): register predicate + mode-aware effective rubric (gate-only auto-exclude)"
```

---

### Task 4: Backfill driver `scripts/score_semantic.py`

**Files:**

- Create: `scripts/score_semantic.py`
- Test: `tests/test_score_semantic_script.py`

**Interfaces:**

- Consumes: `app.semantic.enumerate_semantic_work`/`evaluate_outputs`; `app.judge_render.contact_sheet_path`/`render_contact_sheets`; `scripts.judge_capture.browser_capture_multi_factory`; `app.config`; `app.database.SessionLocal`/`init_db`.
- Produces: `scripts/score_semantic.py` with `CONDITION = "turntable"`, `_sheet_provider(db, capture_multi) -> sheet_for`, `_build_client()`, `_capture_multi()`, `main() -> int`.

**Design note:** Cloned from `scripts/score_completeness.py` (the proven template). Persistence is unconditional; `emit_flags = (config.SEMANTIC_ADMISSIBILITY_MODE == "advisory")`. The **acceptance run (Task 5) sets `BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off`** so it persists verdicts to cross-tab without writing advisory flags into the copy DB. The only non-glue logic is `_sheet_provider`'s cached-else-render behavior — that is what Step 1 tests; the full script is exercised for real in Task 5.

- [ ] **Step 1: Write the failing test** (`tests/test_score_semantic_script.py`)

```python
# tests/test_score_semantic_script.py
import scripts.score_semantic as ss


def test_condition_is_turntable_for_cache_reuse():
    # Must match app.completeness / score_completeness so cached sheets are reused.
    assert ss.CONDITION == "turntable"


def test_sheet_provider_reuses_cached_without_rendering(tmp_path, monkeypatch):
    from app import config

    # Point ASSET_DIR at a temp dir and pre-place a cached turntable sheet for output 7.
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    renders = tmp_path / "renders"
    renders.mkdir()
    (renders / "7_turntable.png").write_bytes(b"CACHED")

    called = {"render": 0}

    def fake_render(db, ids, condition, *, capture_multi):
        called["render"] += 1
        return {"rendered": 0, "errors": 0, "failures": []}

    monkeypatch.setattr(ss, "render_contact_sheets", fake_render)

    sheet_for = ss._sheet_provider(db=None, capture_multi=lambda *a, **k: [])
    data = sheet_for(7)
    assert data == b"CACHED"
    # render_contact_sheets is idempotent, so calling it is allowed, but the cached bytes are read.
```

(Note: `render_contact_sheets` is imported into the `scripts.score_semantic` namespace so `monkeypatch.setattr(ss, "render_contact_sheets", ...)` intercepts it. If the implementation calls it before reading, that is fine — it is idempotent and returns immediately for an existing sheet; the assertion is on the bytes read from the cache path.)

- [ ] **Step 2: Run it, verify it fails**

Run: `.venv/bin/pytest tests/test_score_semantic_script.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.score_semantic'`.

- [ ] **Step 3: Create `scripts/score_semantic.py`**

```python
# scripts/score_semantic.py
"""Batch-score semantic admissibility for outputs. Renders (or reuses) a turntable contact sheet
per output, runs the semantic VLM judge, persists an Admissibility(predicate='semantic') row.
Persistence is unconditional; advisory flags are emitted only when the configured mode is
'advisory'. Build the Anthropic client from ANTHROPIC_API_KEY (as scripts/judge_vlm.py does).

NEVER set BIO3D_DATABASE_URL=study. For the acceptance run, point BIO3D_DATABASE_URL at a COPY of
the study DB and set BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off (persist verdicts, no advisory flags)."""

from __future__ import annotations

import argparse
import os
import sys

from app import config
from app.database import SessionLocal, init_db
from app.judge_render import contact_sheet_path, render_contact_sheets
from app.semantic import enumerate_semantic_work, evaluate_outputs

CONDITION = "turntable"  # same as completeness -> cached sheets are reused


def _sheet_provider(db, capture_multi):
    """Render (idempotently) then read the turntable contact-sheet PNG bytes for an output."""

    def sheet_for(output_id: int) -> bytes:
        render_contact_sheets(db, [output_id], CONDITION, capture_multi=capture_multi)
        path = os.path.join(config.ASSET_DIR, contact_sheet_path(output_id, CONDITION))
        with open(path, "rb") as f:
            return f.read()

    return sheet_for


def _build_client():
    import anthropic

    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _capture_multi():
    from scripts.judge_capture import browser_capture_multi_factory

    return browser_capture_multi_factory()


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-score semantic admissibility.")
    ap.add_argument("--limit", type=int, default=0, help="score at most N outputs (0 = all)")
    args = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        work = enumerate_semantic_work(db)
        if args.limit:
            work = work[: args.limit]
        emit_flags = config.SEMANTIC_ADMISSIBILITY_MODE == "advisory"
        sheet_for = _sheet_provider(db, _capture_multi())
        summary = evaluate_outputs(
            db, work, client=_build_client(), sheet_for=sheet_for, emit_flags=emit_flags
        )
        db.commit()
    print({"mode": config.SEMANTIC_ADMISSIBILITY_MODE, **summary})
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_score_semantic_script.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/score_semantic.py tests/test_score_semantic_script.py
git commit -m "feat(semantic): backfill driver (turntable sheets, cached-else-render, mode-gated flags)"
```

---

### Task 5: Acceptance run + results doc + mode-flip decision (real-execution)

**Files:**

- Create: `docs/results/2026-07-03-semantic-admissibility-results.md`
- Modify (conditionally, only if zero-FP passes): `app/config.py` default `"advisory"` → `"gate"`

**Nature:** This is the **real-execution acceptance gate**, not a TDD task. It requires `ANTHROPIC_API_KEY`, the Playwright browser (contact-sheet capture), a COPY of the study DB, and the real GLB assets. If those are unavailable in the execution environment, this task is **BLOCKED pending the key/assets** — the code (Tasks 1–4) ships regardless, and the predicate is safe (`advisory` default never auto-excludes). Do not fabricate results; if you cannot run it, report BLOCKED with exactly what is missing.

- [ ] **Step 1: Make an isolated copy of the study DB + locate assets**

```bash
# NEVER score the real study DB. Copy it (stop any server writing to it first; copy -wal too if present).
cp data/study/arena-study.db "$CLAUDE_JOB_DIR/tmp/audit-semantic.db"
# Real GLB assets root (the bio3d-arena-mvp worktree used by the structural acceptance run):
#   set BIO3D_DATA_DIR to the assets root that contains assets/ + renders/ for these outputs.
```

- [ ] **Step 2: Run the backfill over the copy in `off` mode (persist, no advisory flags)**

```bash
ANTHROPIC_API_KEY=... \
BIO3D_DATA_DIR=<assets-root> \
BIO3D_DATABASE_URL="sqlite:///$CLAUDE_JOB_DIR/tmp/audit-semantic.db" \
BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off \
  .venv/bin/python scripts/score_semantic.py
```

Expected: a summary dict `{"mode": "off", "scored": <n>, "errors": <e>, "flagged": 0, ...}`. Investigate if `errors` is more than a small handful (unreadable assets).

- [ ] **Step 3: Cross-tab the verdicts against the 32 flags + good outputs**

Query the copy DB (read-only): join `Admissibility(predicate='semantic')` to `Completeness.category` and to the human `OutputFlag` set. Compute:

- **False positives (merge-blocker):** count of outputs with `admit=0` (semantic) whose `Completeness.category = 'complete'` — MUST be **0**. Also sample ~20 known-good outputs and confirm none are rejected.
- **Recall:** of the 31 semantically-flagged outputs (the human ⚑ set minus the 1 structural already caught), how many semantic rejects — broken out by `reason` (`multiple`/`sub_part`/`not_a_plant`/`wrong_species`).
- **Reason distribution** over all rejects.

- [ ] **Step 4: Write `docs/results/2026-07-03-semantic-admissibility-results.md`**

Follow the structure of `docs/results/2026-07-03-structural-admissibility-results.md`: run parameters; result headline (zero-FP pass/fail); rejects-by-reason table; recall-vs-the-32-flags table; honest interpretation; reproduce block. Report the numbers as measured — no rounding up, no hiding false positives.

- [ ] **Step 5: Decide the mode + flip if earned**

- If **zero false positives on good outputs**: change `app/config.py` default from `"advisory"` to `"gate"`:
  ```python
  SEMANTIC_ADMISSIBILITY_MODE = os.environ.get(
      "BIO3D_SEMANTIC_ADMISSIBILITY_MODE", "gate"
  ).lower()
  ```
  and run `.venv/bin/pytest -q` to confirm the suite stays green with the new default (in particular `test_semantic_pool.py` and `test_pool_autogate.py`).
- If **any false positive**: keep `"advisory"`, and record in the results doc exactly which good outputs were wrongly rejected and the suspected cause (prompt over-trigger on a specific class). The predicate ships advisory; a follow-on tightens the prompt.

- [ ] **Step 6: Commit**

```bash
git add docs/results/2026-07-03-semantic-admissibility-results.md app/config.py
git commit -m "results(semantic): acceptance run — <zero-FP verdict>, recall <k>/31 flags; mode=<advisory|gate>"
```

---

## Self-Review

- **Spec coverage:** predicate core + verdict enum + precision-first uncertain→admit (Task 1); enumerate breadth reaching UNSCORED + persistence reuse + advisory non-hiding flag + SemanticPredicate (Task 2); composer registration + mode-aware gate-only auto-exclude + pool parity (Task 3); backfill driver reusing turntable sheets (Task 4); zero-FP acceptance run + results doc + mode flip (Task 5). All spec sections mapped.
- **Placeholder scan:** every code step contains complete code; the only deferred content is Task 5's measured numbers (inherent to a real-execution acceptance run, not a placeholder).
- **Type consistency:** `evaluate_outputs(..., emit_flags: bool)` used identically in Tasks 2/4; `enumerate_semantic_work(db) -> list[{"output_id","taxon"}]` consumed unchanged in Task 4; `verdict_from_code(code, note="")` and `score_semantic(client, png, *, taxon)` consistent across Tasks 1/2; `SEMANTIC_FLAG_SESSION`/`ADVISORY_NO_HIDE_THRESHOLD` defined Task 1, used Task 2; `record_flag(db, output_id, session_id, reason, threshold)` matches the live signature; `DEFAULT_RUBRIC` unchanged, `_effective_rubric` appends semantic only in gate mode.
