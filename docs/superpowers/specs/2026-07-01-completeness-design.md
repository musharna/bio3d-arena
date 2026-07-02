# D-Complete: organism-level biological completeness metric — design

> 2026-07-01. A reference-free, per-output metric scoring whether a generated 3D output is a
> **complete, valid plant** (taxon-expected organs present) vs an isolated fruit / partial organ /
> fragment — orthogonal to Chamfer AND to human preference. Operationalizes the project's
> "geometry-is-not-enough" finding (24.4% of outputs morphologically incomplete; geometric metrics
> pass a clean isolated fruit). Grounded in the next-directions triage (OWNED-AT-SEAM; moat = the
> per-taxon trait rubrics + the already-labeled incomplete outputs). v1 = a **validated metric
> artifact**, no arena wiring.

## Goal

For each generated `ModelOutput` of a known taxon, produce (a) a **completeness category** —
`complete` / `partial-organism` / `isolated-organ` / `fragment` — and (b) a continuous
**completeness score** (fraction of the taxon's required organs present), from a reference-free VLM
read of the model's rendered views against an authored per-taxon expected-organ inventory. Then
**validate** the metric against the project's existing human incomplete-labels.

## Non-goals (YAGNI)

- No arena wiring in v1: no leaderboard/board, no `/difficulty`-style page, no vote-pool gating,
  no exclusion of incomplete outputs from matchmaking. (Those are natural follow-ons; not now.)
- No D-Gen generator-improvement loop (this metric is D-Gen's future reward, but the loop is separate).
- No taxa beyond the 6 Mode-C study taxa (that's where both rubrics and human labels exist).
- No new rendering pipeline — reuse the existing judge contact sheets.
- No per-organ confidence calibration beyond `present` / `absent` / `uncertain`.

## Constraints

- **Reference-free:** the score must NOT require a GT scan/mesh (unlike Chamfer/`Metric`). It reads
  only the generated output's own rendered views + the authored inventory.
- **Orthogonal-by-construction:** the metric must be able to flag a geometrically-clean, un-fragmented
  isolated fruit as incomplete — that separation is the whole point and the validation's headline.
- **Reuse existing infra:** VLM tool-use pattern from `input_grade.py`; contact-sheet rendering from
  `judge_render.py`; per-output persistence pattern from the `Metric` model; `JUDGE_MODEL` from `judge`.
- **Framing discipline (anti-recapitulation):** this is the ORGANISM-LEVEL missing-organ-inventory
  axis, NOT a generic "plausibility/realism" score (SRAM arXiv:2512.01373 and the liver-ML metric
  arXiv:2508.02482 own generic/single-organ realism). The per-taxon required-organ inventory is what
  keeps it non-recapitulating.

## Verified existing APIs (read live on master @699d700)

- **`app/input_grade.py`** — the VLM tool-use template. `grade_with_vlm(client, image_bytes, *,
growth_form, strategy_entry) -> dict`: builds a message with an image block + text, calls
  `client.messages.create(model=JUDGE_MODEL, max_tokens=400, tools=[GRADE_TOOL],
tool_choice={"type":"tool","name":"record_input_grade"})`, and parses the `record_input_grade`
  `tool_use` block (raises if absent). `GRADE_TOOL` is a dict with `name` + `input_schema`.
- **`app/judge_render.py`** — `render_contact_sheets(db, output_ids, condition, *, capture_multi) ->
{"rendered", "errors", "failures"}` writes `renders/{output_id}_{condition}.png` under
  `config.ASSET_DIR`, **idempotently** (skips outputs whose sheet exists). `contact_sheet_path(output_id,
condition)` gives the relative path; `tile_contact_sheet(pngs, cols, rows) -> bytes` tiles tiles.
- **`app/judge.py`** — exports `JUDGE_MODEL`. Judge tool-use verdict pattern (`record_verdict`).
- **`app/models.py`** — outputs are `ModelOutput` (table `model_output`). **`Metric`** (table `metric`)
  is the precedent for a per-output objective score: "one row per output (latest)", FK to
  `model_output`, nullable float fields (`fscore`…), a `species_verdict` String(8), `scorer_version`
  String. `Completeness` will mirror this shape.
- **`app/trait_morphology.py`** — `build_morphology_rubric(taxon, *, sparql_fn)` and authored traits
  with `organ_shape` `trait_class` per taxon (tomato `fruit_form`+`leaf_form`; pine `needle_form`+
  `cone_form`; rose `leaf_form`+`fruit_form`; soybean `leaf_form`+`pod_form`; arabidopsis
  `leaf_form`+`fruit_form(silique)`; + one more). These name the taxon's salient organs — the seed
  for the inventory (but NOT a full completeness inventory; see Component 1).
- **`data/study/calibration_labels*.csv`** — schema `output_id, trait_key, trait_class, taxon,
expected, contact_sheet, human_verdict, note`. **Trait-level**, not per-output: the incompleteness
  signals ("only a fruit", "partial plant", "not a plant", "isolated organ") live in free-text
  `note`/`human_verdict`. Ground-truth per-output labels must be DERIVED from these (Component 5).

## Components

### 1. Organ inventory — `app/organ_inventory.py` (new)

A pure module (no DB). `ORGAN_INVENTORY: dict[str, TaxonInventory]` keyed by the 6 Mode-C taxa. Each:

```python
@dataclass(frozen=True)
class Organ:
    key: str          # "foliage", "reproductive_fruit", "vegetative_axis"
    visual: str       # short VLM descriptor, e.g. "round red fleshy fruit(s)"
    required: bool     # required for a "complete" organism vs optional/seasonal

@dataclass(frozen=True)
class TaxonInventory:
    taxon: str
    organs: tuple[Organ, ...]
```

- Seeded from a **generic plant skeleton** (every taxon requires: `vegetative_axis` = stem/trunk,
  `foliage` = leaves/needles) PLUS the taxon's **reproductive/ distinctive organ(s)** taken from the
  rubric's `organ_shape` traits (tomato→`reproductive_fruit` berry; pine→`reproductive_cone` optional
  [seasonal]; soybean→`reproductive_pod`; etc.).
- `required` marks organs whose absence makes a plant incomplete; seasonal organs (a pine cone) are
  `required=False`. This required/optional split is the authored judgment — small, explainable.
- Helper: `inventory_for(taxon) -> TaxonInventory | None` (None → taxon not covered → scorer skips).
- Every `Organ.visual` is a concrete, image-judgeable phrase (a completeness read must be visual).

### 2. VLM completeness scorer — `app/completeness.py` (new)

Mirrors `input_grade.grade_with_vlm`:

- `COMPLETENESS_TOOL` — a tool dict `name="record_completeness"`, `input_schema` with an array
  `organs_present`: one entry per inventory organ `{key, status: "present"|"absent"|"uncertain"}`,
  plus a free-text `note`. The prompt lists the taxon's expected organs (key + visual) and instructs
  the VLM to mark each over the contact sheet, then call `record_completeness`.
- `score_completeness(client, sheet_bytes, *, taxon, inventory) -> dict` — one VLM call
  (`model=JUDGE_MODEL`, `tools=[COMPLETENESS_TOOL]`, forced `tool_choice`), parse the
  `record_completeness` block (raise if absent, matching `input_grade`'s strictness). Returns the raw
  per-organ status dict + note.

### 3. Derivation — `app/completeness.py`

`derive(inventory, organs_present) -> (category, score)` — pure, unit-tested:

- `score` = (# required organs with status `present`) ÷ (# required organs). `uncertain`/`absent`
  count as not-present for the score.
- Let `present_count` = # organs (required or optional) with status `present`. Category is decided
  purely on the `present`/`absent`/`uncertain` statuses the VLM emits (there is no "partial" status):
  - `complete` — all **required** organs `present`.
  - `partial-organism` — `present_count` ≥ 2 but ≥1 required organ is not `present` (a recognizable
    plant missing a part).
  - `isolated-organ` — exactly 1 organ `present` (a single organ — a lone fruit, or a bare axis — is
    not an organism, regardless of which organ it is).
  - `fragment` — 0 organs `present`.
- These four rules are total and mutually exclusive over `present_count ∈ {0, 1, ≥2}` (≥2 splits on
  whether all required are present). `uncertain` counts as not-present everywhere, so it never upgrades
  a category (conservative).

### 4. Persistence — `Completeness` model in `app/models.py` (mirrors `Metric`)

`__tablename__ = "completeness"`, one row per `ModelOutput` (latest wins, upsert): `output_id`
FK→`model_output` (indexed, unique), `category` String(20), `score` Float nullable, `checklist_json`
Text (the raw per-organ statuses + note, for audit/explainability), `judge_model` String(128),
`scorer_version` String(64), `created_at`. Alembic-free schema-create path consistent with how
`Metric` is created (follow the project's existing table-creation convention — `Base.metadata`).

### 5. Scoring pass + read API

- **Service fn** `score_outputs(db, output_ids, *, client, capture_multi) -> summary` in
  `app/completeness.py`: for each output — ensure a contact sheet exists (call
  `render_contact_sheets` for the `completeness` condition; idempotent), load the sheet bytes,
  `score_completeness`, `derive`, upsert a `Completeness` row. Fail-loud per output (record failure,
  continue), summary `{scored, skipped_no_inventory, errors, failures}`.
- **Batch entry** `scripts/score_completeness.py` — CLI over a taxon/ set of outputs (mirrors existing
  `scripts/*_runner.py`). No admin UI in v1 (YAGNI); an admin `/admin/recompute`-style hook is a
  follow-on.
- **Read API** `GET /api/completeness.json` — per-output `{output_id, taxon, generator, category,
score}` (mirrors the existing `/api/trait_scores.json` / `/api/procedural.json` endpoints). This is
  the v1 "surface": data only.

### 6. Validation harness — `scripts/validate_completeness.py` + `docs/results/...`

The crux of "validated metric".

- **Derive ground-truth** per-output categories from `data/study/calibration_labels*.csv`: group the
  trait-level rows by `output_id`; map free-text `human_verdict`/`note` incompleteness flags
  ("not a plant"/"only a fruit"/"isolated"/"partial"/"incomplete" → the 4-way category; otherwise
  `complete`). This mapping is a small **explicit keyword table** in the script, applied over the
  notes; ambiguous/empty-note outputs are dropped from the eval set (reported, not silently). ⚠ This
  is noisy free-text → the derived GT is itself approximate; the script prints the mapped label next
  to each note so the mapping is auditable, and the count dropped.
- **Metrics:** on a held-out split of labeled outputs, report **accuracy + Cohen's κ** for (i) the
  binary `complete` vs `not-complete` collapse (primary) and (ii) the full 4-way category (secondary),
  plus **isolated-organ recall** (did we catch the "just a fruit" cases — the headline).
- **Baseline contrast (moat proof):** for the same outputs, compute a generic geometry signal — a
  no-fragmentation / connected-components check on the mesh (and Chamfer/`fscore` from `Metric` where a
  GT exists) — and show it does NOT separate the human-labeled isolated-organ / partial cases from
  `complete`. This demonstrates orthogonality to geometry (the non-recapitulation claim).
- Writes `docs/results/2026-07-01-completeness-validation-results.md` with the numbers + the
  auditable GT mapping table.

## Data flow

`ModelOutput` (GLB, known taxon) → `render_contact_sheets(condition="completeness")` → sheet PNG →
`score_completeness` (VLM organ checklist) → `derive` → upsert `Completeness{category, score,
checklist_json}` → `/api/completeness.json`. Offline, no GT, no votes. Validation reads the persisted
`Completeness` rows + the derived-GT from the calibration CSVs.

## Error handling / edge cases

- **Taxon not in inventory** → `score_outputs` skips (counts `skipped_no_inventory`); never guesses.
- **Contact-sheet render fails** → recorded in `failures`, output skipped, loop continues (fail-loud,
  not silent).
- **VLM returns no `record_completeness` block** → raise (matches `input_grade`); the batch catches
  per-output and records the failure.
- **All organs `uncertain`** → score 0, category derived conservatively (won't be `complete`); the
  note is retained for audit.
- **Output has no derivable GT label** (empty/ambiguous note) → excluded from the validation eval set
  and counted, never coerced to `complete`.

## Testing

- **Unit (synthetic fixtures)** `tests/test_completeness_derive.py`: feed `derive` hand-built
  `organs_present` lists → assert the exact `(category, score)` for each of the 4 categories + the
  `uncertain`-doesn't-upgrade rule + the isolated-organ (non-axis single-organ) rule. Inventory-lookup
  (`inventory_for`) unknown-taxon → None.
- **Unit (parse)** `tests/test_completeness_scorer.py`: a fake Anthropic client returning a canned
  `record_completeness` tool_use block → assert `score_completeness` parses it; a response with no such
  block → assert it raises.
- **Real-execution check** (per the repo's real-execution doctrine): a live run of
  `score_outputs` on TWO known outputs from the labeled set — one human-labeled `complete` plant and one
  human-labeled isolated-fruit — asserting the derived category matches the human label. Run as part of
  the validation script's smoke path (not a mocked unit test), so at least one real VLM read is exercised.

## Success criteria

- **PASS (v1 validated):** binary `complete` vs `not-complete` **κ ≥ 0.6** on the held-out labeled
  split. Ship the metric with the binary distinction as validated.
- **Fallback:** if the fine 4-way category κ is < 0.6 but the binary κ clears 0.6, ship the binary
  gate as validated and mark the 4-way category **experimental** (as Mode-C did). Persist both; label
  the 4-way clearly in the API/results.
- **Baseline contrast must hold:** the geometry baseline must visibly fail to separate the
  isolated-organ cases the metric catches (else the orthogonality claim is unsupported → reconsider).

## Open decisions (defaults chosen)

1. Primary goal = **validated metric artifact**, no arena wiring. Chosen.
2. Mechanism = **VLM expected-organ checklist** over reused judge contact sheets. Chosen.
3. Inventory = **authored compact per-taxon** (generic skeleton + rubric-derived reproductive organ),
   6 Mode-C taxa, required/optional split. Chosen.
4. Output = **both** a 4-way category and a continuous fraction. Chosen.
5. Success bar = **binary κ ≥ 0.6 primary; 4-way experimental fallback**. Chosen.
6. GT source = **derived from the trait-level calibration CSV notes** via an auditable keyword mapping;
   ambiguous outputs dropped-and-counted. Chosen (with the noise caveat surfaced).
