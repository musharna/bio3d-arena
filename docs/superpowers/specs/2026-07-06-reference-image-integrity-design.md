# Reference-Image Integrity Subsystem — Design Spec

> Status: design approved (brainstorm 2026-07-06). Next: implementation plan → SDD build.
> Branch: `reference-image-integrity` off `master` (@002eff0).

## Goal

Make the reference images voters see a **fair** and **high-quality** fidelity anchor:

1. stop showing a recon output's own input photo as a "reference" (circularity/bias);
2. ensure the independent ground-truth gallery is **complete** (every task) and **quality-gated**
   (no fruit-only / wrong-species / poor-exemplar images);
3. add a CLIP/BioCLIP judge capability and _measure_ — via one feasibility probe — where it
   actually pays before productionizing any render-based use.

## Motivating findings (grounded, 2026-07-06)

- `reference_images_for_task` (app/service.py:1157) surfaces the recon **input photo first**,
  then the CC gallery — so for an `image_recon` output, one "reference" IS its own input. That
  is circular: a recon that reproduces its input scores as faithful even if biologically wrong.
- Only **11 of 18 tasks** have an independent gallery manifest. 7 tasks (tomato ×2, barley, rose,
  gourd/Cucurbita, Hericium, Morchella) currently rely on the input photo as their only reference
  → excluding inputs without completing galleries would regress them to _zero_ references.
- `semantic.py:24-28`: `wrong_species` was **dropped** from the admissibility gate — "0 flags
  caught, 3 of 4 true FPs" — the Claude VLM is unreliable at species identity. An **independent**
  judge family (BioCLIP) is the textbook fix, _if_ it survives the real-photo→render domain gap.
- BioCLIP is trained on TreeOfLife-10M (real organism **photos**). Reference/input photos are
  in-domain; 3D render-sheets are out-of-domain. This split governs which uses we trust vs must test.

## Scope decisions (from brainstorm)

- **Item 1 (input-vs-reference):** FULLY EXCLUDE the recon input from the vote UI (uniform, by
  principle) — not a collapsed disclosure. Gated on gallery completion. Text→3D unaffected (never
  contributed an image input). Resolves rose/soybean circularity uniformly, not as a non-CC hack.
- **Item 2 (fruit-only):** it is an organ-coverage question. Mechanism (completeness-VLM vs
  CLIP zero-shot) is **decided by the probe**, not pre-assigned.
- **Item 3 (species-rep judge):** build BOTH generic-CLIP and BioCLIP and a Claude-VLM cross-check;
  **prefer BioCLIP** for production; the probe validates.
- **Input subject-verification:** FOLDED IN (in-domain, high value — catches the gourd_ref
  wrong-subject bug class before recon spend).
- **Revive `wrong_species` on outputs (#3):** PROBE-ONLY decision gate this round. Build only if
  BioCLIP-on-renders clears the species-separation test. Perceptual-fidelity (#4) and text→3D
  alignment (#5) stay LOGGED as paper-direction follow-ons — out of scope here.

## Components & build order

Dependency graph (→ = depends on):

```
C (feasibility probe) ──┐
A (gallery completion) ─┼─→ B (gallery QA gate) ──→ E (exclude input from vote UI)
                        └─→ D (input subject-verification gate)
```

- **C** uses EXISTING data (11 galleries, 128 input photos, GT renders) → starts immediately.
- **A** is independent sourcing → parallel with C.
- **B, D** consume C's chosen mechanism.
- **E** requires A complete (else exclusion blanks 7 tasks).

### Component C — CLIP/BioCLIP feasibility probe (do FIRST; decides the rest)

A single offline experiment. **GPU work → submit via jobd (`--gpu`), never raw** (CLAUDE.md).
Verify the current BioCLIP checkpoint with a web search at build time (BioCLIP-2 may supersede
the CVPR'24 release); pin whatever is current. New dep: `open_clip_torch`.

Labeled evaluation set (hand-label, ~20–30 images from existing assets):

- gallery images tagged `good` / `fruit_only` / `wrong_species` / `poor_exemplar`;
- a few recon input photos incl. the known gourd_ref wrong-subject case (now fixed — use the
  pre-fix image from git history if needed) vs good inputs;
- render-sheets of outputs with KNOWN species, plus deliberately mismatched (right-species render
  labeled against a wrong species name) to measure render separability.

Three mechanisms per applicable item:

- **generic OpenCLIP** (ViT-L/14 LAION) zero-shot with composition prompts
  (`"a whole {common} plant with leaves and stem"` vs `"only the {fruit/cap/etc} of {common}"`);
- **BioCLIP** zero-shot / nearest-taxon for species-rep + species-ID;
- **completeness-VLM** (`completeness.score_completeness` on the image, existing infra).

Report (probe writes a markdown + CSV under `docs/paper/` or `docs/superpowers/`):

- confusion matrix per mechanism per defect (fruit-only, wrong-species, poor-exemplar);
- **species-separation on renders**: does BioCLIP separate right vs wrong species on render-sheets
  at all? This is the GO/NO-GO gate for #3 (and the leading indicator for #4/#5).
- Decision output: the production mechanism for B and D, and the #3 go/no-go.

Acceptance: probe runs end-to-end on the labeled set and emits the decision table. No production
wiring in this component.

### Component A — gallery completion (parallel)

Source CC iNaturalist ground-truth galleries for the 6 uncovered taxa (7 tasks): tomato
(Solanum lycopersicum), barley (Hordeum vulgare), rose (Rosa), gourd (Cucurbita pepo), lion's
mane (Hericium erinaceus), morel (Morchella esculenta). Reuse `scripts/source_reference_gallery.py`

- `source_reference_sidecars.py`. Each image gets a CC license + attribution in `manifest.json`
  (same schema as existing galleries). Every new image passes Component B's QA before it ships.

Acceptance: all 18 tasks resolve a non-empty gallery via `_gallery_slug(task.title)`; licenses
pass the existing `check_licenses` allowlist.

### Component B — gallery QA gate

Using C's chosen mechanism(s): score every gallery image for organ-coverage (fruit-only) and
species-representativeness. Images failing threshold are marked (a `quality` field / `passed_qa`
flag in the manifest, or moved to a `rejected/` sidecar) so `reference_images_for_task` only
emits QA-passed images. Fail-loud: an unscored image is NOT silently shipped.

Acceptance: the pumpkin-only Cucurbita case is flagged and excluded; a labeled good image passes;
`reference_images_for_task` returns only QA-passed gallery images.

### Component D — recon input subject-verification gate

Using C's chosen in-domain mechanism (expected CLIP/BioCLIP): verify a recon **input photo**
actually depicts the task's claimed species before/at ingestion. A mismatch raises an advisory
flag (same non-hiding advisory pattern as `semantic.py`'s `SEMANTIC_FLAG_SESSION`) surfacing to
the review queue — it does NOT auto-hide (precision-first; a human confirms). Backfill-scan the
existing 128 input-bearing outputs and report mismatches.

Acceptance: a deliberately wrong-subject input is flagged; correct inputs are not; the 128-output
scan produces a triage list, zero auto-hides.

### Component E — exclude recon input from the vote UI

Modify `reference_images_for_task` (app/service.py) to DROP the input-photo contribution entirely;
return only QA-passed independent gallery images. Remove the now-dead `cleared_reference_taxa`
input-suppression path if it becomes redundant. Gated on A (all tasks have galleries).

Acceptance: for every task, the vote UI shows ≥1 independent gallery image and NO
`meta.input_image` photo; rose/soybean no longer special-cased; existing tests updated.

## Testing strategy

- C: unit test the prompt-builder + score-parsing on fixtures; the probe run itself is a jobd
  script with a real-execution check on a tiny sample (real BioCLIP forward pass, not mocked).
- A: manifest-schema + license-allowlist tests (mirror existing gallery tests).
- B: fixture image → known organ-coverage verdict; `reference_images_for_task` filters unpassed.
- D: fixture wrong-subject → advisory flag row; correct-subject → none; DB-safe (test DB guard).
- E: `reference_images_for_task` returns no input photos + non-empty gallery per task; paradigm
  agnostic; regression test that text→3D references are unaffected.

## Out of scope (logged)

- #4 perceptual biological fidelity metric (BioCLIP dist render↔photo vs Chamfer) — paper follow-on.
- #5 text→3D prompt-render CLIP alignment — paper follow-on.
- Both gated behind C's render-separability result; see
  [[clip-bioclip-reuse-triage-2026-07-06]].

## Risks

- **Render domain gap** may sink all render-based uses (#3/#4/#5). C measures this once, cheaply,
  before any of them is built. In-domain uses (gallery QA, input verification) are unaffected.
- **BioCLIP checkpoint churn** — verify current model at build (web search); pin it.
- **GPU contention** — probe + any BioCLIP inference goes through jobd `--gpu` with the standard
  full-process-table probe first (CLAUDE.md).
