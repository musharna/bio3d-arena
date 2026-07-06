# Plant → Organism Generalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the organism-judging gates from "plant" to "organism" so valid fungi/animals aren't wrongly excluded — fixing the live fungi mis-gating and unblocking the animal kingdom.

**Architecture:** Taxon-parameterize the semantic-admissibility gate's prompt + rename `not_a_plant`→`not_the_organism` (gate + human ⚑-flag path); add a `COLONIAL_TAXA` frozenset so bracket-fungi clusters aren't flagged `multiple`; generalize the completeness prompt wording; then re-score the fungi so the gate change takes effect on the study DB.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2.0, Anthropic VLM (`claude-sonnet-4-6`), vanilla JS.

## Global Constraints

- Branch `plant-to-organism-generalization` off `master` (already created). Do NOT branch again.
- Minimal / taxon-parameterized (YAGNI): NO `kingdom` field. Do NOT touch `app/scope.py`, generation-side prompts (`commission.py`/`dgen.py`/`agentic.py`), or cosmetic template wording — those are explicitly out of scope.
- Rename value is exactly `not_the_organism` (snake_case, ≤32 chars for the DB column).
- The semantic gate judges PNG render contact sheets — `_img_block`'s `image/png` media type is correct; do NOT change it.
- `verdict_from_code` admits iff the code is NOT in `REJECT_CODES` (precision-first: unknown codes admit). Keep that invariant.
- **Task 4 (re-score) prerequisite:** it runs `scripts/score_semantic.py`, which on this branch is the fragile master version. The robust version (render/VLM timeouts + chunked commits) is on **PR #18** (`scripts-bootstrap-shim`). Merge PR #18 to master and rebase this branch on it (or cherry-pick PR #18's `score_semantic.py`+`render_one_sheet.py`) BEFORE Task 4. Tasks 1–3 do not depend on it.

---

### Task 1: Semantic gate — taxon-parameterized + colonial-aware

**Files:**

- Modify: `app/semantic.py` (`REJECT_CODES`:28, `SEMANTIC_TOOL`:36-50, `_build_messages`:67-79; add `COLONIAL_TAXA`)
- Test: `tests/test_semantic_generalization.py`

**Interfaces:**

- Produces: `COLONIAL_TAXA: frozenset[str]`; `REJECT_CODES` now contains `not_the_organism` (not `not_a_plant`); `_build_messages(png, taxon)` unchanged signature but taxon-parameterized + colonial-aware.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_semantic_generalization.py
from app import semantic


def test_reject_codes_generalized():
    assert "not_the_organism" in semantic.REJECT_CODES
    assert "not_a_plant" not in semantic.REJECT_CODES
    assert semantic.SEMANTIC_TOOL["input_schema"]["properties"]["verdict"]["enum"] == [
        "ok", "multiple", "sub_part", "not_the_organism", "uncertain"
    ]
    assert "plant" not in semantic.SEMANTIC_TOOL["description"].lower()


def test_prompt_is_taxon_parameterized_no_plant():
    msg = semantic._build_messages(b"\x89PNG", "Boletus edulis")
    text = msg[0]["content"][0]["text"]
    assert "Boletus edulis" in text
    assert "plant" not in text.lower()  # generalized to organism


def test_prompt_colonial_clause_only_for_colonial_taxa():
    colonial = semantic._build_messages(b"\x89PNG", "Trametes versicolor")[0]["content"][0]["text"]
    unitary = semantic._build_messages(b"\x89PNG", "Boletus edulis")[0]["content"][0]["text"]
    assert "cluster of the same species" in colonial.lower()
    assert "cluster of the same species" not in unitary.lower()


def test_verdict_not_the_organism_rejects():
    v = semantic.verdict_from_code("not_the_organism", "a blob")
    assert v.admit is False and v.reason == "not_the_organism"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_semantic_generalization.py -q`
Expected: FAIL (`not_a_plant` still present; prompt still says "plant"; no colonial clause).

- [ ] **Step 3: Implement in `app/semantic.py`**

Change `REJECT_CODES` (line 28):

```python
REJECT_CODES = {"multiple", "sub_part", "not_the_organism"}
```

Add after `REJECT_CODES` (the colonial registry):

```python
# Taxa whose natural unit is a cluster/colony (modular organisms: bracket/shelf fungi; future
# coral / colonial animals). For these a same-species cluster is ONE valid subject — the gate must
# not flag it `multiple`. Unitary organisms (a discrete individual) are not listed.
COLONIAL_TAXA = frozenset({"Trametes versicolor"})
```

Change `SEMANTIC_TOOL` description (line 38) and enum (line 44):

```python
    "description": "Judge whether the rendered model is a single, whole, valid specimen of the target organism.",
    ...
                "enum": ["ok", "multiple", "sub_part", "not_the_organism", "uncertain"],
```

Replace `_build_messages` (lines 67-79):

```python
def _build_messages(png: bytes, taxon: str | None) -> list[dict]:
    subject = taxon if taxon else "the organism"
    of_taxon = f" of {taxon}" if taxon else ""
    colonial_clause = (
        " A natural cluster of the SAME species (e.g. shelf/bracket fungi that grow in overlapping "
        "rosettes) is a SINGLE valid subject — do NOT call that `multiple`."
        if taxon in COLONIAL_TAXA
        else ""
    )
    text = (
        f"This is a contact sheet of a generated 3D model{of_taxon}, rendered from several angles "
        f"on a neutral gray background. Judge whether it is a SINGLE, WHOLE, VALID specimen of "
        f"{subject}. "
        "Reject as: `multiple` (more than one DISTINCT organism, or a cluttered scene with "
        f"distractors);{colonial_clause} "
        "`sub_part` (only a detached part — a single organ or appendage — not a whole organism); "
        f"`not_the_organism` (not a recognizable {subject} at all — a blob or unrelated object)"
        ". Otherwise answer `ok`. If you genuinely cannot tell, answer `uncertain`. "
        "Reject ONLY when clearly inadmissible; when in doubt, prefer `ok` or `uncertain`. "
        "Then call record_admissibility."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img_block(png)]}]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_semantic_generalization.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/semantic.py tests/test_semantic_generalization.py
git commit -m "feat(organism): taxon-parameterize semantic gate + COLONIAL_TAXA + not_the_organism"
```

---

### Task 2: Human ⚑-flag reason rename (`not_a_plant` → `not_the_organism`)

**Files:**

- Modify: `app/schemas.py:20`, `app/models.py:679`, `app/static/arena.js:349`, `app/main.py:612`, `app/static/viewer.js:65`
- Test: `tests/test_flag_reason.py`

**Interfaces:**

- Consumes: nothing. Produces: `FlagIn.reason` default+pattern use `not_the_organism`; `OutputFlag.reason` default `not_the_organism`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flag_reason.py
import pytest
from pydantic import ValidationError

from app.schemas import FlagIn


def test_flag_default_is_not_the_organism():
    assert FlagIn(output_id=1).reason == "not_the_organism"


def test_flag_accepts_new_reason_rejects_old():
    assert FlagIn(output_id=1, reason="not_the_organism").reason == "not_the_organism"
    assert FlagIn(output_id=1, reason="failed").reason == "failed"
    with pytest.raises(ValidationError):
        FlagIn(output_id=1, reason="not_a_plant")
```

(If `FlagIn` has required fields beyond `output_id`, read `app/schemas.py` and supply them — grep `class FlagIn` first.)

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_flag_reason.py -q`
Expected: FAIL (default is `not_a_plant`; old value still accepted).

- [ ] **Step 3: Implement the renames**

`app/schemas.py:20`:

```python
    reason: str = Field(default="not_the_organism", pattern="^(not_the_organism|failed|other)$")
```

`app/models.py:679`:

```python
    reason: Mapped[str] = mapped_column(String(32), default="not_the_organism")
```

`app/static/arena.js:349`:

```javascript
      body: JSON.stringify({ output_id: outputId, reason: "not_the_organism" }),
```

`app/main.py:612` — the flag-endpoint docstring/summary text "not-a-plant / failed" → "not the organism / failed" (read the line first; change only the human-readable string, not logic).

`app/static/viewer.js:65` — the aria-label "Flag: not a plant / failed" → "Flag: not the organism / failed".

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_flag_reason.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/schemas.py app/models.py app/static/arena.js app/main.py app/static/viewer.js tests/test_flag_reason.py
git commit -m "feat(organism): rename human flag reason not_a_plant -> not_the_organism"
```

---

### Task 3: Completeness VLM prompt wording

**Files:**

- Modify: `app/completeness.py` (tool description line ~44; `_build_messages` prompt line ~74)
- Test: `tests/test_completeness_prompt_organism.py`

**Interfaces:**

- Consumes: `app/organ_inventory.inventory_for`. Produces: nothing (wording only).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_prompt_organism.py
from app import completeness
from app.organ_inventory import inventory_for


def test_completeness_prompt_says_organism_not_plant():
    inv = inventory_for("Boletus edulis")
    assert inv is not None
    msg = completeness._build_messages(b"\x89PNG", inv)
    text = msg[0]["content"][1]["text"] if msg[0]["content"][1]["type"] == "text" else msg[0]["content"][0]["text"]
    assert "Boletus edulis" in text
    assert "plant" not in text.lower()
```

(Read `completeness._build_messages` first to confirm content ordering; the assertion picks the text block robustly.)

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_completeness_prompt_organism.py -q`
Expected: FAIL ("a generated 3D model of the plant Boletus edulis").

- [ ] **Step 3: Implement in `app/completeness.py`**

Line ~44 (`COMPLETENESS_TOOL` description) "…visible in the rendered plant model." → "…visible in the rendered model."

Line ~74 (`_build_messages`): "a generated 3D model of the plant {inventory.taxon}, " → "a generated 3D model of {inventory.taxon}, ".

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_completeness_prompt_organism.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/completeness.py tests/test_completeness_prompt_organism.py
git commit -m "feat(organism): completeness prompt says organism not plant"
```

---

### Task 4: Re-score fungi with the fixed gate (controller data-op — NOT a TDD task)

**This is a controller-executed step, not a subagent implementation task.** No TDD.

**Prerequisite (see Global Constraints):** the robust `scripts/score_semantic.py` (PR #18) must be on this branch — merge PR #18 to master + rebase, or cherry-pick `score_semantic.py`+`render_one_sheet.py`. Verify `grep -q "render_one_sheet" scripts/score_semantic.py` before proceeding.

- [ ] **Step 1:** Snapshot + copy the study DB to a safe target (marker "test" in the path per `config.is_safe_test_db_target`), e.g. `$CLAUDE_JOB_DIR/tmp/arena-study-rescore-test.db`.
- [ ] **Step 2:** On the copy, delete the stale fungi `semantic` admissibility rows so `enumerate_semantic_work` re-enqueues them:

```sql
DELETE FROM admissibility WHERE predicate='semantic' AND output_id IN (
  SELECT o.id FROM model_output o JOIN task t ON t.id=o.task_id
  WHERE t.title LIKE 'Amanita muscaria%' OR t.title LIKE 'Boletus edulis%'
     OR t.title LIKE 'Hericium erinaceus%' OR t.title LIKE 'Lycoperdon perlatum%'
     OR t.title LIKE 'Morchella esculenta%' OR t.title LIKE 'Trametes versicolor%');
```

- [ ] **Step 3:** Re-run scoped to fungi (sheets cached → VLM-only, fast): `BIO3D_DATABASE_URL=sqlite:///<copy> BIO3D_DATA_DIR=/home/mjarnold/bio3d-arena/data python scripts/score_semantic.py`.
- [ ] **Step 4:** Verify: the 3 former `not_the_organism` fungi and all _Trametes versicolor_ now `admit`; unitary puffball/Amanita/Boletus clusters remain `multiple` (expected — pending the single-specimen data follow-on). Report the before/after admit counts per fungus taxon.
- [ ] **Step 5:** Promote the copy → real study DB (checkpoint WAL, snapshot, cp), matching the established disposition/gallery-promotion pattern.

---

## Self-Review

- **Spec coverage:** A→Task 1; B (`COLONIAL_TAXA`)→Task 1; C→Task 2; D→Task 3; E→Task 4. All spec components covered.
- **Placeholder scan:** Task 4's "controller data-op, no TDD" is intentional (a DB op, like the earlier disposition/gallery promotions), not a placeholder. The "read the line first" notes for main.py/viewer.js/schemas are guardrails for exact strings the grep already located, not TBDs.
- **Type consistency:** `not_the_organism` used verbatim in Tasks 1 (REJECT_CODES, enum, prompt) and 2 (schemas default+pattern, models default, arena.js). `COLONIAL_TAXA` frozenset defined in Task 1, referenced only there. `_build_messages(png, taxon)` signature unchanged.
