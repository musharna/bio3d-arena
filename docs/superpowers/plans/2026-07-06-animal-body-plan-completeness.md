# Animal Body-Plan Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the completeness metric to complement-aware animal body plans so an anatomically-wrong output (a 3-legged dog) is caught as a new `malformed` category.

**Architecture:** Add an expected-`complement` field to `Organ`, an `_animal_inv` constructor + 4 animal taxa; have the completeness VLM report per-part complement status; extend `derive()` to a `malformed` category when all part-types are present but a complement is off; register `malformed` in the severity/label enumerations. Plants/fungi behavior stays byte-identical (all complement=1).

**Tech Stack:** Python 3, SQLAlchemy 2.0, Anthropic VLM (`claude-sonnet-4-6`).

## Global Constraints

- Branch `animal-body-plan-completeness` off `master` (already created). Do NOT branch again.
- `Organ.complement` defaults to 1 — every existing `Organ(...)`, `_inv`, `_body_inv`, and plant/fungi taxon MUST be unaffected (regression-test this).
- The complement judgment is a RELATIVE "full set present / one clearly missing", NOT an exact count (VLMs are unreliable at exact counting). Enum values: `full`/`missing_some`/`extra`/`uncertain`.
- New category value is exactly `malformed`. `malformed` = all required part-TYPES present but a complement not `full`.
- `malformed` is VOTABLE (a fidelity signal), NOT pool-gated: do NOT add it to `config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES` (currently `{isolated-organ, fragment}`). It only needs to render in the scorecard.
- `score` semantics unchanged (required part-type coverage): a `malformed` output scores 1.0; the CATEGORY carries the anatomical signal.
- Validation on real animal renders is DEFERRED to SP3 (no animal outputs exist yet). This plan ships a mock-tested metric only.

---

### Task 1: `Organ.complement` + `_animal_inv` constructor + 4 animal taxa

**Files:**

- Modify: `app/organ_inventory.py` (`Organ` dataclass:23-27; add `_animal_inv` + 4 `ORGAN_INVENTORY` entries)
- Test: `tests/test_animal_inventory.py`

**Interfaces:**

- Produces: `Organ(key, visual, required, complement=1)`; `_animal_inv(taxon, *parts: tuple[str, str, int]) -> TaxonInventory`; `ORGAN_INVENTORY` gains `Canis lupus familiaris`, `Anas platyrhynchos`, `Danaus plexippus`, `Carassius auratus`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_animal_inventory.py
from app.organ_inventory import Organ, inventory_for


def test_organ_complement_defaults_to_one():
    assert Organ("leg", "a leg", True).complement == 1  # existing callers unaffected


def test_dog_inventory_has_four_legs():
    inv = inventory_for("Canis lupus familiaris")
    assert inv is not None
    leg = next(o for o in inv.organs if o.key == "leg")
    assert leg.complement == 4 and leg.required is True


def test_animal_taxa_all_present():
    for t in ("Canis lupus familiaris", "Anas platyrhynchos", "Danaus plexippus", "Carassius auratus"):
        assert inventory_for(t) is not None


def test_existing_plant_inventory_unchanged():
    inv = inventory_for("Solanum lycopersicum")
    assert all(o.complement == 1 for o in inv.organs)  # plants: every part singular
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_animal_inventory.py -q`
Expected: FAIL (`Organ` has no `complement`; animal taxa absent).

- [ ] **Step 3: Implement in `app/organ_inventory.py`**

Add the field to the frozen dataclass (lines 23-27):

```python
@dataclass(frozen=True)
class Organ:
    key: str
    visual: str
    required: bool
    complement: int = 1  # expected count of this part (legs=4, wings=2); 1 = a singular part
```

Add the constructor (after `_body_inv`):

```python
def _animal_inv(taxon: str, *parts: tuple[str, str, int]) -> TaxonInventory:
    """Bilaterian animal body plan: several required parts, each with an expected complement
    (leg x4, wing x2). A part is satisfied only if present AND its full complement is present; an
    all-part-types-present body with a missing limb reads `malformed` (see completeness.derive).
    UNCALIBRATED cross-kingdom extension of the plant-calibrated completeness metric."""
    return TaxonInventory(taxon=taxon, organs=tuple(Organ(k, v, True, c) for k, v, c in parts))
```

Add the 4 taxa to `ORGAN_INVENTORY` (after the fungi block):

```python
    "Canis lupus familiaris": _animal_inv(
        "Canis lupus familiaris",
        ("head", "a head with muzzle, eyes, and ears", 1),
        ("trunk", "a four-legged body/torso", 1),
        ("leg", "a leg", 4),
        ("tail", "a tail", 1),
    ),
    "Anas platyrhynchos": _animal_inv(
        "Anas platyrhynchos",
        ("head", "a head with a flat bill and eyes", 1),
        ("body", "a plump body/torso", 1),
        ("wing", "a wing", 2),
        ("leg", "a webbed leg/foot", 2),
        ("tail", "a short tail", 1),
    ),
    "Danaus plexippus": _animal_inv(
        "Danaus plexippus",
        ("head", "a head with two antennae", 1),
        ("thorax", "the thorax (mid-body segment)", 1),
        ("abdomen", "the abdomen (rear body segment)", 1),
        ("wing", "a wing", 4),
        ("leg", "a leg", 6),
    ),
    "Carassius auratus": _animal_inv(
        "Carassius auratus",
        ("head", "a head with eyes and mouth", 1),
        ("body", "a streamlined fish body", 1),
        ("caudal_fin", "the tail/caudal fin", 1),
        ("pectoral_fin", "a side (pectoral) fin", 2),
        ("dorsal_fin", "the top (dorsal) fin", 1),
    ),
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_animal_inventory.py -q` → PASS (4 passed).

- [ ] **Step 5: Commit** — `git add app/organ_inventory.py tests/test_animal_inventory.py && git commit -m "feat(animal): Organ.complement + _animal_inv + dog/mallard/monarch/goldfish inventories"`

---

### Task 2: Complement reporting in the completeness VLM tool + prompt

**Files:**

- Modify: `app/completeness.py` (`COMPLETENESS_TOOL`:42-63; `_build_messages`:71-79)
- Test: `tests/test_completeness_complement_prompt.py`

**Interfaces:**

- Consumes: `Organ.complement` (Task 1). Produces: the tool schema accepts an optional `complement` field; `_build_messages` lists complements + instructs reporting.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_complement_prompt.py
from app import completeness
from app.organ_inventory import inventory_for


def test_tool_schema_accepts_complement():
    props = completeness.COMPLETENESS_TOOL["input_schema"]["properties"]["organs_present"]["items"]["properties"]
    assert set(props["complement"]["enum"]) == {"full", "missing_some", "extra", "uncertain"}


def test_prompt_lists_complement_and_instructs_reporting():
    inv = inventory_for("Canis lupus familiaris")
    text = completeness._build_messages(b"\x89PNG", inv)[0]["content"][0]["text"]
    assert "expect 4" in text  # the dog's legs
    assert "complement" in text.lower()
    assert "do not count exactly" in text.lower() or "not an exact count" in text.lower()
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_completeness_complement_prompt.py -q` → FAIL.

- [ ] **Step 3: Implement in `app/completeness.py`**

In `COMPLETENESS_TOOL`, add to the `organs_present` item `properties` (keep `required` as `["key", "status"]`):

```python
                        "complement": {
                            "type": "string",
                            "enum": ["full", "missing_some", "extra", "uncertain"],
                        },
```

Replace `_build_messages` (lines 71-79):

```python
def _build_messages(png: bytes, inventory: TaxonInventory) -> list[dict]:
    lines = "\n".join(
        f"- {o.key}: {o.visual}" + (f" (expect {o.complement})" if o.complement > 1 else "")
        for o in inventory.organs
    )
    text = (
        f"This is a contact sheet of a generated 3D model of {inventory.taxon}, "
        "rendered from several angles. For EACH expected part below, mark whether it is visibly "
        "present in the model (present / absent / uncertain). For any part with an expected count "
        "(e.g. 'expect 4'), ALSO set `complement`: `full` if the whole set is present, "
        "`missing_some` if one or more are clearly missing, `extra` if there are clearly more than "
        "expected, or `uncertain`. Do NOT count exactly — judge whether the full set is there. "
        "Judge only what you can see; a rendering of a single detached part should mark the others "
        f"absent.\n\nExpected parts:\n{lines}\n\nThen call record_completeness."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img_block(png)]}]
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_completeness_complement_prompt.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add app/completeness.py tests/test_completeness_complement_prompt.py && git commit -m "feat(animal): completeness VLM reports per-part complement status"`

---

### Task 3: Complement-aware `derive()` + `malformed` category

**Files:**

- Modify: `app/completeness.py` (`derive`:15-39)
- Test: `tests/test_completeness_malformed.py`

**Interfaces:**

- Consumes: `Organ.complement` (Task 1); `organs_present` items may carry an optional `complement` key (Task 2). Produces: `derive` returns category `malformed` for all-present-but-complement-off.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_completeness_malformed.py
from app.completeness import derive
from app.organ_inventory import inventory_for


def _present(inv, complement_overrides=None):
    ov = complement_overrides or {}
    out = []
    for o in inv.organs:
        item = {"key": o.key, "status": "present"}
        if o.complement > 1:
            item["complement"] = ov.get(o.key, "full")
        out.append(item)
    return out


def test_all_present_full_complement_is_complete():
    inv = inventory_for("Canis lupus familiaris")
    cat, score = derive(inv, _present(inv))
    assert cat == "complete" and score == 1.0


def test_missing_leg_is_malformed_not_complete():
    inv = inventory_for("Canis lupus familiaris")
    cat, score = derive(inv, _present(inv, {"leg": "missing_some"}))  # 3-legged dog
    assert cat == "malformed"
    assert score == 1.0  # all part-types present -> coverage 1.0; category carries the signal


def test_missing_whole_part_type_is_partial_not_malformed():
    inv = inventory_for("Canis lupus familiaris")
    present = [{"key": o.key, "status": ("absent" if o.key == "head" else "present"),
               **({"complement": "full"} if o.complement > 1 else {})} for o in inv.organs]
    cat, _ = derive(inv, present)
    assert cat == "partial-organism"  # a whole part-type absent, not malformed


def test_plant_inventory_never_malformed():
    inv = inventory_for("Solanum lycopersicum")
    present = [{"key": o.key, "status": "present"} for o in inv.organs]
    cat, _ = derive(inv, present)
    assert cat == "complete"  # plants: all complement 1 -> complements trivially full
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_completeness_malformed.py -q` → FAIL (no `malformed` branch).

- [ ] **Step 3: Implement — replace `derive` in `app/completeness.py` (lines 15-39)**

```python
def derive(inventory: TaxonInventory, organs_present: list[dict]) -> tuple[str, float]:
    """Map a per-part present/absent/uncertain checklist (with optional `complement` status for
    multi-part organs) to (category, score). Categories: fragment / isolated-organ /
    partial-organism / malformed / complete. `malformed` = every required part-TYPE present but a
    part's expected complement is not `full` (a 3-legged dog); the anatomical-completeness signal
    geometry misses. score = required part-type coverage (a malformed output still scores 1.0)."""
    by_key = {o["key"]: o for o in organs_present}
    required = [o for o in inventory.organs if o.required]
    req_present = sum(1 for o in required if by_key.get(o.key, {}).get("status") == "present")
    score = req_present / len(required) if required else 0.0
    present_count = sum(
        1 for o in inventory.organs if by_key.get(o.key, {}).get("status") == "present"
    )

    def _complement_ok(o) -> bool:
        # A part with expected complement <= 1 (all plants/fungi, singular animal parts) is
        # trivially satisfied; a multi-part organ must report complement `full`.
        if o.complement <= 1:
            return True
        return by_key.get(o.key, {}).get("complement", "full") == "full"

    all_required_present = req_present == len(required)
    complements_full = all(
        _complement_ok(o) for o in required if by_key.get(o.key, {}).get("status") == "present"
    )

    if present_count == 0:
        category = "fragment"
    elif all_required_present and complements_full:
        category = "complete"
    elif all_required_present:  # every part-type present but a limb/wing complement is off
        category = "malformed"
    elif present_count == 1:
        category = "isolated-organ"
    else:
        category = "partial-organism"
    return category, score
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_completeness_malformed.py -q` → PASS (4 passed).

- [ ] **Step 5: Commit** — `git add app/completeness.py tests/test_completeness_malformed.py && git commit -m "feat(animal): complement-aware derive() with malformed category"`

---

### Task 4: Register `malformed` in category enumerations (severity + label + docs)

**Files:**

- Modify: `app/completeness_validation.py` (`_SEVERITY`:~80 and the GT category list ~28-58), `app/models.py:331` (category comment)
- Test: `tests/test_malformed_registered.py`

**Interfaces:**

- Consumes: the `malformed` category (Task 3). Produces: `_SEVERITY` has a `malformed` rank so category comparisons don't `KeyError`.

**Read first:** `app/completeness_validation.py` `_SEVERITY` and the GT category list — malformed ranks BETWEEN `partial-organism` and `complete` (a missing limb is more complete than a missing whole part-type, less complete than a full organism). Confirm the exact severity integers before editing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_malformed_registered.py
from app.completeness_validation import _SEVERITY


def test_malformed_has_severity_between_partial_and_complete():
    assert "malformed" in _SEVERITY
    assert _SEVERITY["partial-organism"] < _SEVERITY["malformed"] < _SEVERITY["complete"]
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_malformed_registered.py -q` → FAIL (`malformed` not in `_SEVERITY`).

- [ ] **Step 3: Implement**

In `app/completeness_validation.py`, add `malformed` to `_SEVERITY` ranked between `partial-organism` and `complete`. The current map is `{"fragment": 0, "isolated-organ": 1, "partial-organism": 2, "complete": 3}` — re-rank `complete` up and insert `malformed`:

```python
_SEVERITY = {"fragment": 0, "isolated-organ": 1, "partial-organism": 2, "malformed": 3, "complete": 4}
```

(Verify the current values by reading the file first; keep any comment. If the GT category list in that module is a fixed set used to validate labels, add `"malformed"` there too so an animal GT label validates.)

In `app/models.py:331`, update the category comment to include `malformed`:

```python
    )  # complete|malformed|partial-organism|isolated-organ|fragment
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_malformed_registered.py -q` → PASS.

- [ ] **Step 5: Full-suite regression gate** — `python -m pytest -q -p no:cacheprovider` → Expected: all pass (confirms plants/fungi completeness + the `_SEVERITY` consumers are unaffected). Fix any test that hard-codes the old category set.

- [ ] **Step 6: Commit** — `git add app/completeness_validation.py app/models.py tests/test_malformed_registered.py && git commit -m "feat(animal): register malformed category in severity + docs"`

---

## Self-Review

- **Spec coverage:** A (Organ.complement + \_animal_inv + taxa)→Task 1; B (tool+prompt)→Task 2; C (derive+malformed)→Task 3; malformed enumeration/scorecard→Task 4; malformed-is-votable-not-gated→Global Constraints (no config change); validation deferred to SP3→noted. Covered.
- **Placeholder scan:** Task 4 says "verify the current `_SEVERITY` values by reading the file" — that's a guardrail for exact integers the plan already estimated `{fragment:0…complete:3}`, not a TBD. No other placeholders.
- **Type consistency:** `Organ(key, visual, required, complement=1)` used in Task 1 and consumed as `o.complement` in Tasks 2-3. `complement` status enum `full/missing_some/extra/uncertain` identical in Task 2 (schema) and Task 3 (`_complement_ok` checks `== "full"`). Category `malformed` verbatim in Tasks 3-4. `_animal_inv(taxon, *parts: tuple[str,str,int])` signature matches the 4 taxa calls.
