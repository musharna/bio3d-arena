# Plant → Organism Generalization — Design Spec

> Status: design approved (brainstorm 2026-07-06). Next: implementation plan → SDD build.
> Branch: `plant-to-organism-generalization` off `master`.
> Sub-project 1 of 3 in the animal-kingdom addition (the others: animal body-plan completeness; animal taxa generation).

## Goal

Generalize the organism-judging gates from "plant" to "organism" so valid non-plant specimens (fungi today, animals next) are not wrongly rejected — fixing a **live bug** where the semantic-admissibility gate excludes ~2/3 of fungi from the vote pool, and unblocking the animal kingdom.

## Motivation (grounded, 2026-07-06)

The semantic backfill (populating `Admissibility(predicate='semantic')`, mode=`gate`, which excludes rejected outputs from the vote pool) scored 340 outputs. Of **42 fungi outputs, 28 were rejected**:

- **3 as `not_a_plant`** — the VLM prompt asks for "a single, whole, valid **plant** specimen," so a mushroom is correctly-per-prompt but wrongly-per-arena rejected. **This is the plant→organism bug.**
- **25 as `multiple`** — VLM notes confirm genuine same-species clusters ("a cluster of multiple distinct _Lycoperdon perlatum_"): puffball 7, Boletus 8, Amanita 8, Hericium 2, all `image_recon` (recon reproduced clustered input photos). The gate is _correct_ here; this splits into a data follow-on (single-specimen inputs for **unitary** taxa) and a gate change for **colonial** taxa.

Kingdom is currently implicit (`_inv` vs `_body_inv` in `app/organ_inventory.py`); there is **no `kingdom` field**. Per the brainstorm, we keep it that way (YAGNI): fix the gate by **taxon-parameterization**, not a kingdom hierarchy.

## Design decisions (from brainstorm)

1. **Minimal / taxon-parameterized** — the VLM knows what a _{taxon}_ is; the gate says "a valid specimen of _{taxon}_", no kingdom field. Defer `scope.py`'s botanical parts vocabulary + generation-side prompts.
2. **`colonial` per-taxon biological flag** (like the existing `repro_required`) — for organisms whose natural unit is a cluster/colony (bracket fungi like _Trametes versicolor_; future coral/colonial animals), a same-species cluster is ONE valid subject. Unitary taxa (puffball, Amanita, Boletus) keep single-subject-strict → fixed via a data follow-on.
3. **Rename `not_a_plant` → `not_the_organism`** across BOTH the semantic gate AND the human ⚑-flag path (approved).

## Components

### A. Semantic gate — taxon-parameterized + colonial-aware (`app/semantic.py`)

- **`SEMANTIC_TOOL` description (line 38-ish):** "…a single, whole, valid **plant** specimen." → "…a single, whole, valid specimen of the target organism."
- **enum (line 44):** `["ok", "multiple", "sub_part", "not_a_plant", "uncertain"]` → replace `not_a_plant` with `not_the_organism`.
- **`REJECT_CODES` (line 28):** `{"multiple", "sub_part", "not_a_plant"}` → `{"multiple", "sub_part", "not_the_organism"}`.
- **`_build_messages(png, taxon)` prompt (function ~line 67; the "plant" usages span ~lines 71-78, incl. `not_a_plant` at line 74):** replace the four "plant" usages with taxon/organism wording:
  - "Judge whether it is a SINGLE, WHOLE, VALID specimen of {taxon}." (falls back to "organism" when `taxon` is None).
  - `multiple`: "more than one DISTINCT organism, or a cluttered scene with distractors" — **and** when `taxon in COLONIAL_TAXA`, append: "A natural cluster of the SAME species (e.g. shelf/bracket fungi) is a single valid subject — do NOT call that `multiple`."
  - `sub_part`: "only a detached part — a single organ/appendage — not a whole organism."
  - `not_the_organism`: "not a recognizable {taxon}/organism at all — a blob or unrelated object."
- No signature change: `_build_messages` consults the module-level `COLONIAL_TAXA` set (below). `score_semantic(client, sheet_png, *, taxon)` is unchanged.

### B. `COLONIAL_TAXA` registry (`app/semantic.py`)

`COLONIAL_TAXA: frozenset[str] = frozenset({"Trametes versicolor"})` — taxa whose natural unit is a cluster/colony. Extensible (future colonial animals). One-line comment on the biological rationale (modular vs unitary organisms).

### C. Human ⚑-flag reason (`not_a_plant` → `not_the_organism`)

- `app/schemas.py:20` — `reason: str = Field(default="not_a_plant", pattern="^(not_a_plant|failed|other)$")` → default `"not_the_organism"`, pattern `"^(not_the_organism|failed|other)$"`.
- `app/models.py:679` — `OutputFlag.reason` default `"not_a_plant"` → `"not_the_organism"`. Existing rows keep their historical value (not re-validated); no migration needed.
- `app/static/arena.js:349` — `reason: "not_a_plant"` → `"not_the_organism"`.
- `app/main.py:612` + `app/static/viewer.js:65` — user-visible "not a plant / failed" label → "not the organism / failed" (or "wrong or broken").

### D. Completeness VLM prompt (`app/completeness.py`)

- Line 44 (tool description) "visible in the rendered **plant** model" → "…rendered model".
- Line 74 (`_build_messages`) "a generated 3D model of the **plant** {inventory.taxon}" → "…3D model of {inventory.taxon}". (The completeness `derive` math is already organism-agnostic; this is wording only.)

### E. Re-score fungi with the fixed gate

Re-run `scripts/score_semantic.py` scoped to the ~46 fungi outputs (delete their stale `semantic` admissibility rows first so `enumerate_semantic_work` re-enqueues them; the turntable sheets are cached so this is VLM-only, fast). Expected: the 3 `not_the_organism` and the _Trametes versicolor_ clusters now **admit** and return to the vote pool; unitary-cluster puffball/Amanita/Boletus stay `multiple` pending the data follow-on. Run on a study-DB copy, verify, promote (the established pattern).

## Testing

- **A/B:** unit-test `_build_messages` — for a colonial taxon the prompt contains the "natural cluster … single valid subject" clause; for a unitary taxon it does not; the taxon name is interpolated; `None` taxon → "organism". Fake-client test of `score_semantic` returning `not_the_organism` maps to a non-admit verdict.
- **C:** schema test — `FlagIn(reason="not_the_organism")` valid, `reason="not_a_plant"` now invalid; default is `not_the_organism`.
- **D:** `completeness._build_messages` prompt no longer contains "plant"; contains the taxon.
- **E:** a data-op (script run on a copy), verified by counts, not a unit test.

## Out of scope (deferred, logged)

- `app/scope.py` botanical parts vocabulary (`whole_plant/foliage/flower/fruit`, `is_plant`) — kingdom-conditional rework, only needed for animal Mode-C traits (sub-project 2/3).
- Generation-side prompts (`commission.py`, `dgen.py`, `dgen_ab.py`, `agentic.py`) — plant-_generation_; animal generation is sub-project 3.
- Cosmetic template/UI "plant" wording (benchmark/spotlight/difficulty/procedural templates, seed category name/criterion) — light-touch later.
- Single-specimen fungi input re-sourcing (puffball/Amanita/Boletus) — data follow-on to fully clear the `multiple` rejects for unitary taxa.

## Risks

- **Renaming `not_a_plant`** leaves historical `OutputFlag` rows and the 3 semantic `not_a_plant` admissibility rows with the old value. Handled by re-scoring fungi (E) for the semantic rows; historical human-flag rows are harmless (display-only). No live public users yet (pre-launch), so no in-flight client sends the old code.
- **Colonial list completeness** — only _Trametes versicolor_ today; if other current taxa are genuinely colonial they'd still mis-`multiple`. The fungi mechanism data (above) shows the rest are unitary, so the list is correct for the current roster.
