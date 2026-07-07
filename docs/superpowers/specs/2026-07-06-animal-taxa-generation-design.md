# Animal Taxa Generation + Ingestion (SP3) — Design Spec

> Status: design approved (brainstorm 2026-07-06); reference photos SOURCED + registered.
> Branch: `animal-taxa-generation`, stacked on `animal-body-plan-completeness` (SP2, PR #20) —
> SP3 needs SP2's `ORGAN_INVENTORY` entries for the 4 taxa.
> Sub-project 3 of 3 in the animal-kingdom addition (SP1 plant→organism DONE PR#19; SP2
> completeness DONE PR#20). Makes the `malformed` metric non-inert by putting real animal outputs
> in the DB + vote pool.

## Goal

Generate real 3D outputs for the 4 SP2 taxa across `image_recon` + `text_native`, ingest them into
the study DB, completeness-score + difficulty-tier them, so animal matchups enter the vote pool and
the complement-aware `malformed` category gets its first live validation.

## Scope decision (from brainstorm)

- **Paradigms: `image_recon` + `text_native` only** (this wave). Both runnable now (TRIPO/FAL/
  REPLICATE/ANTHROPIC keys present); no external-key or prompt-refactor dependency. Mirrors the
  fungi wave-1 start.
- **Deferred to a later wave/SP:** `procedural_llm` + `agentic` + D-Gen — doubly blocked on
  (a) `OPENROUTER_API_KEY` not exported into the env, and (b) hardcoded "plant" wording in
  `commission.py`/`agentic.py`/`dgen.py`/`dgen_ab.py` (would ask an LLM to build "a dog plant").
- **Rubric depth: bare completeness-scope rubrics only** (`seed_completeness_rubrics.py`, free —
  auto-covers the 4 taxa via existing `ORGAN_INVENTORY`). Full literature `TraitRubric`s
  (`build_trait_rubrics --live`, paid) + animal Mode-C trait scoring (`scope.py` botanical-vocab
  rework) are deferred — no wave-1 benefit.

## Reference photos (DONE — registered + gate-clean)

Sourced from iNaturalist + Wikimedia Commons, eyeball-verified single whole subjects, registered
via `add_reference_photo.py`; all 4 pass `reference_provenance.cleared_reference_taxa()`. Stored
as local runtime assets in `data/assets/reference/{slug}_ref_clean.jpg` + `{slug}_ref.json`
(gitignored `data/`, like all reference photos — travels via the export/promote pipeline, not git):

| slug     | subject                                                     | source                                                   | license      |
| -------- | ----------------------------------------------------------- | -------------------------------------------------------- | ------------ |
| dog      | _Canis lupus familiaris_ — walking domestic dog, all legs   | iNaturalist obs 117208148 (ajott)                        | CC-BY-4.0    |
| mallard  | _Anas platyrhynchos_ — drake standing on land, legs visible | Wikimedia "Mallard Drake standing" (David Horler)        | CC-BY-SA-3.0 |
| monarch  | _Danaus plexippus_ — adult, wings open dorsal               | Wikimedia "Monarch butterfly in BBG" (Rhododendrites)    | CC-BY-SA-4.0 |
| goldfish | _Carassius auratus_ — whole fish, side profile              | Wikimedia "Carassius auratus 197778318" (Jackson Kusack) | CC0-1.0      |

**ShareAlike flag:** mallard + monarch are CC-BY-SA. Fine for the DISPLAY arena; the sidecar `note`
records the ShareAlike obligation that attaches if the **redistribute** dataset ships them. The
standing mallard (legs visible) was chosen deliberately over cleaner CC-BY water shots — a
feet-hidden water photo would make _every_ mallard recon read as leg-missing and pollute the
completeness signal. No clean CC-BY dorsal monarch existed; CC-BY-SA is the only option there.

## Components

### A. Subject-Task creation (infra gap — `app/seed.py`)

No committed script creates subject `Task` rows for a new taxon generically — the fungi wave did it
via uncommitted interactive calls. Fix: add `seed_animal_subjects(db)` mirroring `seed_rose_subject`
(built on the existing `_ensure_subject(db, title, prompt)` helper), creating the 4 plain subject
Tasks (no `ReconTask`, so recon-GT scoring isn't attempted — animals have no held-out GT). Title
form matches the generators' expectation: `"{Binomial} — single-image → 3D reconstruction"`. The
first animal Task flips the existing `"animals"` category (currently a "Coming soon." placeholder in
`seed.CATEGORIES`) live. **Unit-tested** (idempotent; creates 4 Tasks + the category).

### B. Registry wiring (data entries in existing scripts)

- `generate_api_recon.py::CROPS` — 4 entries `{slug: {"task_title": ..., "image":
"data/assets/reference/{slug}_ref_clean.jpg"}}`.
- `generate_api_text.py::TAXA` — 4 `(title, prompt)` tuples (whole-animal prompts).
- `app/difficulty_rubric.py::RUBRIC` — 4 hand-authored 5-axis entries (`canis_lupus_familiaris`,
  `anas_platyrhynchos`, `danaus_plexippus`, `carassius_auratus`). Animals skew high on
  non_rigidity + thin_structure + self_occlusion → expected to tier **hard** (fine; the corpus
  wanted more hard-tier range).
- `source_reference_gallery.py::TAXA` — 4 entries for display galleries (optional, non-blocking).

### C. Generation + ingestion (operational wave-steps, on a study-DB copy)

Run against a **copy** of the study DB (`BIO3D_DATABASE_URL=sqlite:////tmp/…`), never the live study
DB (per `is_safe_test_db_target`; incident 2026-06-28):

1. `generate_api_recon.py --crop {dog,mallard,monarch,goldfish}` — image→3D across the active
   providers. **Costs API spend** (FAL/REPLICATE/TRIPO). Generation + ingestion are fused
   (`ingest.register_output`).
2. `generate_api_text.py --crop {slug}` — text→3D baseline.
3. `seed_completeness_rubrics.py` — creates bare `TraitRubric(taxon=…)` for the 4 taxa (auto, via
   `ORGAN_INVENTORY`).
4. Completeness scoring (VLM, ANTHROPIC key) — the score that surfaces `malformed`.
5. `backfill_paradigms.py --commit` — assign `image_recon`/`text_native` paradigms (fail-loud on
   any unmapped generator).
6. `assign_difficulty.py` — materialize tiers from the new `RUBRIC` entries.

### D. `malformed` live-validation (the SP2 payoff)

Inspect the completeness results for any recon output categorized `malformed`, and eyeball-confirm
it genuinely has a missing/incomplete complement (a dropped leg / fin / the monarch's 6 legs —
recon reliably drops thin structures, so these are the natural producers). Record the outcome:

- ≥1 confirmed `malformed` with a real missing limb → the SP2 metric is **validated** end-to-end.
- 0 malformed → a logged finding (recon didn't produce an anatomically-incomplete-but-whole case),
  NOT a failure — the metric still ran; note it for a targeted follow-up.

### E. Promotion (data deliverable)

After verification on the copy: promote the DB copy back to the study DB (filesystem `cp`, WAL
checkpointed first) + ensure the 4 reference photos are present in the study asset dir; snapshot.
Reference photos + generated GLBs + DB rows are **data** (not git) — they ship via the
export/promote pipeline. The **code** (seed_animal_subjects + registry/RUBRIC entries) is the PR.

## Data flow

reference photo (registered) → `generate_api_recon`/`generate_api_text` → `ingest.register_output`
(GLB + `ModelOutput` + `Generator`, inline structural admissibility) → `seed_completeness_rubrics`
(bare `TraitRubric`) → completeness scoring (`category` incl. `malformed`) → `backfill_paradigms`
(paradigm labels) → `assign_difficulty` (tiers) → vote pool + `/difficulty` grid + completeness
board → promote.

## Testing

- **Component A** (`seed_animal_subjects`): unit-tested — idempotent, creates the 4 Tasks + flips
  the `animals` category; re-run creates nothing new.
- **Components B/C/D** are operational wave-steps validated by inspection (the fungi-wave pattern):
  generation success counts per provider, completeness category distribution, difficulty tiers
  assigned, and the manual `malformed` eyeball-check. No synthetic unit test can stand in for real
  recon output — the real-execution check IS the generation run + inspection.
- Full suite stays green (the code additions are additive; no existing path changes).

## Out of scope (deferred, logged)

- `procedural_llm` / `agentic` / D-Gen paradigms (OPENROUTER export + plant-wording generalization
  in `commission.py`/`agentic.py`/`dgen.py`/`dgen_ab.py`).
- Full literature `TraitRubric`s + animal Mode-C trait scoring (`scope.py` botanical-parts vocab).
- CC-BY-2.0 allowlist widening (avoided by sourcing CC-BY-3.0/4.0/CC0/CC-BY-SA only).
- Public deployment (Task #33 — the go-public step; "public = LAST" per roadmap).

## Risks

- **API spend** — recon across 4 taxa × active providers (FAL/REPLICATE/TRIPO) is real,
  metered spend. Gated on explicit user greenlight before the generation run.
- **Recon quality on animals** — animals are hard recon subjects (non-rigid, thin limbs); low
  fidelity is expected and is exactly what the arena measures (voters + completeness judge). A
  systematically-bad input (e.g. an occluded limb in the ref photo) is triaged by
  `recon_reliability_flags`, not silently accepted.
- **ShareAlike inputs (mallard/monarch)** — DISPLAY-safe; sidecar-flagged for the redistribute
  posture. If the redistribute dataset must stay ShareAlike-free, swap these two for CC-BY/CC0
  before dataset export (a later, non-blocking op).
- **Study-DB safety** — all mutating ops run on a `/tmp` copy first (`is_safe_test_db_target`),
  then promote. Never pytest/generate against `BIO3D_DATABASE_URL=study`.
