# Publish-Safety Posture Completion — Design Spec

> **Status:** design, awaiting review. Terminal state = invoke `writing-plans`.
> **Branch:** `publish-posture-completion` (off master @532e6c6).

**Goal:** Complete the two-posture publish-safety system (built in PR #12) so the public
DISPLAY arena can show the good non-CC-_input_-derived recon (rose/soybean) while the
REDISTRIBUTE dataset stays provably clean, and close the one real gate gap.

**Non-goal:** Rebuild the two-posture export, AI-label, no-download, admissibility, license
normalizer, or `assert_recon_photos_cleared` — all already exist on master.

---

## Background: what already exists on master (do NOT rebuild)

`scripts/export_public.py::export_bundle(posture=...)` composes gates per posture:

- **`redistribute`**: `filter_include_for_posture` (redistributable-license AND **not**
  commercial-model → excludes all `api:/recon:/frontier:` recon) + `check_licenses`
  (output license ∈ `REDISTRIBUTABLE_LICENSES`, fail-loud). `bio3d-arena` source is treated
  as redistributable (own asset).
- **`display`**: `filter_include_for_posture` (redistributable **OR** commercial-model recon)
  - `assert_recon_photos_cleared` (every commercial-model recon's input photo must have a
    cleared CC sidecar, fail-loud) + the gold twin.
- `app/reference_provenance.py`: `cleared_reference_taxa()` (taxa with a valid CC
  `{taxon}_ref.json` sidecar), `assert_recon_photos_cleared(db, output_ids)`.
- `app/main.py::_serialize`: `machine_generated` + `attribution` per output; `references`
  (reference gallery); arena renders the 🤖 AI-generated badge + no download affordance.
- `app/service.py::reference_images_for_task`: builds the reference panel from each **visible**
  output's `meta.input_image` + the CC species gallery. Already excludes `hidden_at` outputs.
- Legal posture-per-source mapping is verified in memory `ai_model_ip_probe_2026-07-05`.

---

## Global Constraints

- No provider ToS forbids benchmarking/comparison; zero patent risk (per the IP probe). The
  DISPLAY arena is legally clear; the tightening is about **input-photo copyright** + **dataset
  redistribution**, not the models.
- All display of AI outputs keeps: **no-download affordance + 🤖 AI-generated label** (exist).
- REDISTRIBUTE stays **fail-loud**: any un-cleared asset aborts the whole export.
- Never weaken `redistribute`. This spec only _loosens `display`_ and _tightens `redistribute`_.
- Study DB is the source of truth; never run pytest/scripts against it directly (use copies;
  `is_safe_test_db_target`). Snapshot before any data mutation.

---

## Design

### Component 1 — Loosen the display gate (decision ①: "show the mesh, never the photo")

**Problem:** `assert_recon_photos_cleared` runs in the `display` branch, so recon whose input
photo lacks a CC sidecar (the good rose/soybean recon, derived from an untraceable product shot
/ a private photo) is blocked from _even display_. The operator chose Option C: show the derived
**mesh** in the arena, but never publicly serve the non-CC **photo**.

**Change A — move the input-photo clearance gate to redistribute-only.**
In `export_bundle`, call `assert_recon_photos_cleared` (and its gold variant) **only** when
`posture == "redistribute"`. The `display` bundle no longer fails on an un-cleared reference
photo. Rationale: displaying a transient, no-download, AI-labeled _derivative mesh_ is the
accepted display-vs-distribute posture; redistribution of the mesh **file** or the **photo**
is where the input-photo license must be clean.

**Change B — suppress non-CC input photos from the reference panel (never show the photo).**
`app/service.py::reference_images_for_task` currently surfaces every visible output's
`meta.input_image` as a "reconstruction input photo." Add a per-image CC-clearance filter:
an `input_image` is shown **only if** its taxon has a cleared CC sidecar
(`reference_provenance.cleared_reference_taxa()` — reuse; do not duplicate the allowlist).
Uncleared inputs (e.g. `reference/rose_ref.jpg`, the private soybean photo) are silently
dropped; the CC species gallery still shows. Result: the arena shows the good mesh + the CC
gallery, never the copyrighted source photo.

- Interface: `reference_images_for_task(db, task)` unchanged signature; internally imports
  `cleared_reference_taxa()` once and skips inputs whose `_taxon_of(img)` ∉ cleared.
- Edge: a CC input (e.g. `tomato_ref_clean.jpg`, cleared) is still shown — the filter only
  removes _un-cleared_ inputs.

**Tests:** display bundle succeeds with an un-cleared-input recon present (was: raised);
redistribute bundle still raises on the same; `reference_images_for_task` drops an un-cleared
input but keeps a cleared input + the gallery.

### Component 2 — Close the Stream-D gap: gate `bio3d-arena` internal recon on redistribute

**Problem:** `assert_recon_photos_cleared` only checks _commercial-model_ sources
(`_COMMERCIAL_MODEL_PREFIXES`). Internal `bio3d-arena` recon is _also_ a derivative of an input
photo, is treated as redistributable, and so can enter the **redistribute** dataset without its
input photo being verified.

**Change:** In `assert_recon_photos_cleared`, also check `bio3d-arena` outputs **that are
recon** — identified by the presence of `meta.input_image` (a recon has one; a held-out GT mesh
/ scan does not). For such an output: if its input taxon is not cleared (or the input is
unrecorded / `input_image is None`), **raise** (unverifiable → excluded from redistribute,
conservative). GT/scan `bio3d-arena` assets (no `input_image`) remain exempt.

- This runs only on the redistribute path (Component 1 moved the gate there), so it never
  blocks display.
- Interface: `assert_recon_photos_cleared` gains no new params; its internal source-filter
  widens from "commercial-model only" to "commercial-model OR (bio3d-arena AND has
  input_image)".

**Tests:** a `bio3d-arena` recon with an un-cleared / None input raises in redistribute; a
`bio3d-arena` GT mesh (no input_image) does not; a `bio3d-arena` recon with a cleared input
passes.

### Component 3 — User-photo ingestion (owned-CC nursery shots)

**Problem:** rose/soybean (and future taxa) have no good _free-CC_ photo, but the operator can
photograph common plants (herbs, vegetables) at nurseries and license them himself (CC0/CC-BY),
yielding a clean input that produces _good_ recon passing **both** postures.

**Change:** `scripts/add_reference_photo.py` — a small CLI that ingests an owned/CC photo:

```
add_reference_photo.py --taxon rose --image /path/to/photo.jpg \
    --author "Jaret Arnold" --license CC0-1.0 --source-url "https://..." \
    [--subject "Rosa (whole flowering plant)"] [--title "..."] [--note "..."]
```

It: (a) validates `--license` normalizes into `reference_provenance._CC_OK`; (b) copies the
image to `{taxon}_ref_clean.jpg` in **both** data dirs (main + worktree, matching existing
convention); (c) writes a `{taxon}_ref_clean.json` sidecar with all `_REQUIRED` fields
(fail-loud if any missing); (d) prints the next step (`generate_api_recon.py --crop {taxon}
--force` then completeness scoring). Idempotent; refuses to overwrite without `--force`.

- Does NOT itself call the paid regen or touch the DB (keeps it safe + composable). Regen +
  scoring stay the operator's explicit follow-up (spend-gated).
- Reuses `reference_provenance._REQUIRED` + `licensing.normalize_license` (no duplicate schema).

**Tests:** writing a valid CC photo produces a sidecar that `cleared_reference_taxa()` accepts;
a non-CC `--license` is rejected fail-loud; missing required field is rejected.

### Component 4 — Disposition data-op (un-hide the good recon)

Not code — a documented, snapshotted study-DB operation (a script under `scripts/` or a
memo-tracked manual op), run once:

- **Un-hide** the good non-CC rose recon (267–274) + soybean recon (244–253) → they re-enter the
  arena; with Component 1 they display (mesh only, photo suppressed) and stay excluded from
  redistribute (commercial-model).
- **Hide** the poor CC rose recon (516–523) + soybean pod recon (524–530) — redundant + weak now
  that the good ones show. (Reversible; keep the CC input files/sidecars.)
- **roma tomato**: leave hidden — tomato already has good _CC_-input recon (`tomato_ref_clean`,
  1.0), so the non-CC roma recon is unneeded.
- Snapshot study PRE/POST; verify via `recon_reliability_flags` + a display-bundle dry-run.

### Component 5 (documented follow-on, NOT built here) — probe-informed redistribute expansion

The current `filter_include_for_posture` excludes **all** commercial-model recon from
redistribute (conservative). The IP probe shows some are redistribute-ok: MIT models
(TRELLIS/TripoSR/PartCrafter) and open Hunyuan-2.0, **especially when served via Replicate**
(fal §6(e)(xiv) bars fal-served files from a dataset regardless). Expanding redistribute to
include those would grow the dataset — but it needs a per-source×per-platform allowlist and the
[COUNSEL] items (Deemos/Tripo tier, Hunyuan territory, CC-BY-SA ShareAlike). Left as a
follow-on so this spec stays focused and low-risk; the conservative filter is safe meanwhile.

---

## File structure

- Modify: `scripts/export_public.py` (gate composition per posture — Component 1A).
- Modify: `app/service.py` (`reference_images_for_task` CC-clearance filter — Component 1B).
- Modify: `app/reference_provenance.py` (`assert_recon_photos_cleared` widen to bio3d-arena
  recon — Component 2).
- Create: `scripts/add_reference_photo.py` (Component 3).
- Create: `scripts/disposition_rose_soybean.py` (Component 4, snapshotted, study-safe).
- Tests: `tests/test_export_postures.py` (extend), `tests/test_reference_image.py` (extend),
  `tests/test_reference_provenance.py` (extend), `tests/test_add_reference_photo.py` (new).

## Testing strategy

Unit-level, no paid APIs: synthetic DB fixtures for the gate/posture/reference tests; a temp
dir for the ingestion script. One real-execution check: a `--dry-run` display + redistribute
`export_bundle` on a study **copy** asserting the expected include/exclude sets (good non-CC
recon in display-not-redistribute; un-cleared bio3d-arena recon out of redistribute).

## Rollout / disposition order

1. Land Components 1–3 (code + tests) via a normal PR.
2. Run Component 4 (disposition) on the study DB (snapshotted) after the code is merged.
3. Re-host the audit server; visually confirm the good rose/soybean recon shows with the input
   photo suppressed and the AI badge present.
