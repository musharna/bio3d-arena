# Animal Body-Plan Completeness — Design Spec

> Status: design approved (brainstorm 2026-07-06). Next: implementation plan → SDD build.
> Branch: `animal-body-plan-completeness` off `master` (which now has the plant→organism generalization, PR #19).
> Sub-project 2 of 3 in the animal-kingdom addition (SP1 = plant→organism generalization, DONE; SP3 = animal taxa generation).

## Goal

Extend the reference-free completeness metric to **animal body plans** with a **complement-aware** signal, so a geometrically-clean but anatomically-wrong output (a 3-legged dog, a one-winged bird) is caught — the sharpest case of the arena's "geometry is not enough" thesis, which the current present/absent-per-organ metric cannot express.

## Motivation

The completeness metric (`app/completeness.py::derive` + `app/organ_inventory.py`) reads per-organ **present/absent/uncertain** and derives a category (`fragment`/`isolated-organ`/`partial-organism`/`complete`). This works for plants (`_inv`, 2 required organs) and single-body organisms (`_body_inv`, 1 required). Animals differ: a bilaterian body plan has **several required parts, some occurring in a fixed complement** (4 legs, 2 wings, 2 antennae). A missing limb leaves every part-_type_ present, so the current metric reads a 3-legged dog as `complete`. Complement-awareness is what makes anatomical completeness measurable.

## Design decisions (from brainstorm)

1. **Complement-aware, "full set present" — NOT exact counts** (VLMs are unreliable at exact counting; a relative "is the full set present, or one clearly missing/extra" is reliable).
2. **New `malformed` category** for "all part-types present but a complement is incomplete/extra" — surfaced separately in the scorecard (it's the headline animal signal), distinct from `partial-organism` (a whole part-type missing).
3. **Taxa (phylo-spread validation slice):** dog (_Canis lupus familiaris_, quadruped), mallard duck (_Anas platyrhynchos_, bird), monarch butterfly (_Danaus plexippus_, insect), goldfish (_Carassius auratus_, limbless fin/tail plan). Droppable to 3 for a leaner first wave.
4. **Uncalibrated cross-kingdom extension** (like `_body_inv` for fungi) — ships with a mock-tested metric; the real "does complement-status catch a missing limb" validation is cross-project with SP3 (needs generated animal renders).

## Components

### A. `Organ.complement` + `_animal_inv` constructor + animal taxa (`app/organ_inventory.py`)

**Add a field to the frozen `Organ` dataclass** (backward-compatible — defaults to 1, so every existing `Organ(key, visual, required)` and both existing constructors are unaffected):

```python
@dataclass(frozen=True)
class Organ:
    key: str
    visual: str
    required: bool
    complement: int = 1  # expected count of this part (legs=4, wings=2); 1 = singular part
```

**New constructor** (alongside `_inv`/`_body_inv`):

```python
def _animal_inv(taxon: str, *parts: tuple[str, str, int]) -> TaxonInventory:
    """Bilaterian animal body plan: several required parts, each with an expected complement
    (leg x4, wing x2). A part is satisfied only if present AND its full complement is present; an
    all-part-types-present body with a missing limb reads `malformed` (the anatomical-completeness
    signal geometry misses). UNCALIBRATED cross-kingdom extension of the plant-calibrated metric."""
    return TaxonInventory(taxon=taxon, organs=tuple(Organ(k, v, True, c) for k, v, c in parts))
```

**Animal taxa entries** in `ORGAN_INVENTORY`:

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
        ("wing", "a wing", 4),  # 2 forewings + 2 hindwings
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

### B. Complement reporting in the VLM tool + prompt (`app/completeness.py`)

**`COMPLETENESS_TOOL`** — add an OPTIONAL `complement` field to each `organs_present` item (only meaningful for parts whose expected complement > 1):

```python
                    "properties": {
                        "key": {"type": "string"},
                        "status": {"type": "string", "enum": ["present", "absent", "uncertain"]},
                        "complement": {
                            "type": "string",
                            "enum": ["full", "missing_some", "extra", "uncertain"],
                        },
                    },
                    "required": ["key", "status"],
```

**`_build_messages`** — list each expected part with its complement, and instruct the VLM to report complement status for multi-complement parts (relative judgment, NOT exact count):

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
        "Judge only what you can see; a rendering of a single detached part should mark the "
        f"others absent.\n\nExpected parts:\n{lines}\n\nThen call record_completeness."
    )
    return [{"role": "user", "content": [{"type": "text", "text": text}, _img_block(png)]}]
```

### C. Complement-aware `derive()` + `malformed` category (`app/completeness.py`)

```python
def derive(inventory: TaxonInventory, organs_present: list[dict]) -> tuple[str, float]:
    by_key = {o["key"]: o for o in organs_present}
    required = [o for o in inventory.organs if o.required]
    req_present = sum(1 for o in required if by_key.get(o.key, {}).get("status") == "present")
    score = req_present / len(required) if required else 0.0
    present_count = sum(1 for o in inventory.organs if by_key.get(o.key, {}).get("status") == "present")

    # A present part with an expected complement > 1 must report complement `full` to be satisfied;
    # a complement<=1 part (all plants/fungi, singular animal parts) is trivially satisfied.
    def _complement_ok(o: Organ) -> bool:
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

**`score` semantics unchanged** (required part-type coverage): a `malformed` animal has all part-types present so `score == 1.0`, and the **category** carries the anatomical signal. This keeps plants/fungi behavior identical (all `complement == 1` → `complements_full` always True → never `malformed`).

### D. Validation (cross-project with SP3)

The metric is unit-testable now with mocked VLM output. The real check — does `complement: missing_some` on an actual 3-legged-dog render route to `malformed`? — needs SP3's generated animal renders; ships as an uncalibrated extension with a validation run once outputs exist (mirrors the fungi wave).

## Testing

- **A:** `Organ` default `complement == 1`; existing `_inv`/`_body_inv` taxa unchanged; `inventory_for("Canis lupus familiaris")` returns a leg-complement-4 inventory.
- **B:** `_build_messages` lists "(expect 4)" for the dog's legs and instructs `complement` reporting; the tool schema accepts a `complement` field.
- **C (the load-bearing tests):** all-parts-present + all complements `full` → `complete`; all-parts-present + `leg: missing_some` → `malformed` (the 3-legged dog); a whole part-type absent → `partial-organism`/`fragment` as before; a plant inventory (all complement 1) is byte-identical to today's behavior (regression).

## Out of scope (deferred, logged)

- `app/scope.py` botanical parts vocabulary — kingdom-conditional, only needed for animal Mode-C trait scoring.
- Exact-count metrics + bilateral left/right asymmetry.
- SP3: animal taxa generation + ingestion (recon/text/procedural_llm), CC reference/input photos, difficulty rubric entries, galleries — the metric is inert until animal outputs exist.
- Single-specimen fungi input re-sourcing; cosmetic template/UI "plant" wording.

## Risks

- **Complement-status VLM reliability** — untested until real animal renders exist (SP3). If `missing_some` proves noisy, fall back to present/absent for the noisy parts (a per-part opt-out), not a metric redesign.
- **`malformed` is votable, NOT pool-gated (resolved).** The completeness pool-exclusion set is `config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES = {"isolated-organ", "fragment"}` (via `flags.excluded_output_ids_by_completeness` → `CompletenessPredicate`). `partial-organism` is deliberately NOT excluded (a partial plant is votable). By the same admissibility-vs-fidelity principle established in SP1, a `malformed` animal (a recognizable single whole organism with a missing limb) is a **fidelity** signal, not an admissibility failure — so `malformed` is **NOT** added to `POOL_EXCLUDED_COMPLETENESS_CATEGORIES`; it stays in the vote pool and voters rate it low. It is surfaced as a distinct category in the completeness scorecard / difficulty grid only. (`score` stays 1.0 for `malformed` = part-type coverage; the category is the anatomical signal.) No gate-config change.
- **Scorecard/UI surfacing** — the completeness board + difficulty grid + any `category`-labeled UI enumerate the known categories; `malformed` must be added to those enumerations/labels so it renders (a new category that no view knows about would be dropped or mislabeled). Grep `partial-organism` across `app/` + templates at build to find the enumerations.
