<!-- ROOT_CAUSE_OK: design spec, not a bug fix -->

# Pre-Publish License & Gate Hardening — Design Spec

**Date:** 2026-07-04
**Status:** approved (brainstorming), pre-plan
**Goal:** Make the public-export pipeline physically incapable of shipping an un-cleared asset, and split publishing into two license postures — a **display** arena (broad, incl. commercial-model recon, no bulk download) and a strict **redistribute** dataset (only genuinely-redistributable assets) — so bio3d can go public defensibly.

## Context

A grounded copyright audit (4 research streams + adversarial re-verification, persisted in `memory/copyright_audit_2026-07-04.md`) found: an existing fail-loud license gate (`public_export.check_licenses`, conservative CC/CC0/ODbL allowlist) that 240/294 outputs currently fail — but overwhelmingly for **fixable** reasons (license-string _format_ mismatch, article-vs-data-record mislabeling, null-license on our own/LLM outputs), not genuine non-redistributability. Only 18 outputs (XfrogPlants commercial, Demeter/AgriGen NC) are truly dead. Two structural gaps: (a) the export does **not** carry or apply the admissibility (structural/completeness/semantic) gate, and (b) the recon paradigm's input reference photos have no recorded provenance. The **Plant Methods (CC-BY) precedent** shows _displaying_ commercial-model plant outputs for comparison is defensible; _redistributing_ a downloadable asset dataset of them is where vendor "no re-release" bars bite.

## Approved decisions

- **Own / LLM / procedural outputs → tag `CC0-1.0`** (most defensible given AI-output copyrightability; per audit).
- **Two-posture architecture** — display arena (broad) + strict downloadable dataset (CC/own only).
- **Reference-photo provenance** — best-effort source via EXIF / git-history / old-sidecar cross-ref / visual-description search; untraceable → fall back to swap-or-user-input; the **fail-loud export gate is the backstop** so nothing uncleared ships.

## Global constraints

- **Fail-loud, never loosen for convenience.** Widening the allowlist is forbidden; the only license-passing changes are (1) _normalizing_ a string to an already-allowlisted SPDX id, (2) correcting a mislabel to the verified license, (3) tagging our own assets. Every gate raises (aborts the export) rather than silently dropping/including.
- **Do not add NC/ND/commercial licenses to any allowlist.**
- The `redistribute` posture's allowlist stays exactly `{CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0, CC-BY-3.0, CC-BY-2.0, PUBLIC-DOMAIN, ODbL-1.0}`.
- Read-only on the real study DB; all scoring/backfill runs on a COPY. NEVER `BIO3D_DATABASE_URL=study`. Test runner `.venv/bin/pytest`.
- Attribution is a live obligation for every CC-BY/CC-BY-SA asset (author + source + license link).

## Architecture

Four units, each independently testable.

### 1. License normalizer (`app/licensing.py`, new)

`normalize_license(raw: str | None) -> str | None` — maps loose/space forms to SPDX: `"CC-BY 4.0"→"CC-BY-4.0"`, `"CC0 1.0"/"CC0"→"CC0-1.0"`, `"CC-BY-SA 4.0"→"CC-BY-SA-4.0"`, Objaverse codes `by→CC-BY-4.0`, `cc0→CC0-1.0`, `by-sa→CC-BY-SA-4.0`; NC/ND variants normalize to their SPDX form (which stays OFF the allowlist → still rejected). Deterministic: uppercase, collapse internal whitespace/underscores to `-`, ensure version suffix. `check_licenses` calls `normalize_license(o.license)` before the allowlist test. Pure function, exhaustively unit-tested against the real DB label set.

### 2. Provenance backfill (`scripts/backfill_licenses.py`, new)

A driver that corrects/assigns the `model_output.license` (+ `attribution`) column on a DB COPY, idempotently:

- **Own/procedural/LLM** (source `bio3d-arena`, `commissioned`, `agentic:*`, `procedural:*` except NC gens, `infinigen`) → set `CC0-1.0` (procedural-tool output is the user's own work per FSF/Blender; LLM output → CC0 per copyrightability). Excludes the NC generators (`procedural:demeter`, `procedural:agrigen`).
- **crops3d** → relabel `CC-BY-NC-ND 4.0` → `CC0-1.0` (verified Figshare data-record license), with an `attribution`/note recording the data-record basis.
- **Objaverse** (21 objs) → fetch each object's real license from its `objaverse_uid` (via the `objaverse` annotations / metadata), normalize, and set it; any `nc*`/`*nd` object → leave non-allowlisted (will be gate-excluded). No network at export time — this backfill records the resolved license into the DB.
- Space-form CC labels → normalized SPDX (via unit 1).
- Fail-loud per row; prints a disposition summary. Never touches XfrogPlants/Demeter/AgriGen (stay non-redistributable).

### 3. Reference-photo provenance enforcement (`app/reference_provenance.py` + `scripts/source_reference_sidecars.py`)

- Extend `tests/test_reference_provenance.py` to parametrize **all** recon taxa (arabidopsis, maize, rose, soybean, tomato, pinus) and assert each `data/assets/reference/{taxon}_ref.jpg` has a valid sidecar (CC-only allowlist, all required fields).
- `scripts/source_reference_sidecars.py` — best-effort sourcing: read each photo, check EXIF `Artist`/`Copyright`, cross-ref the old MVP sidecars + git history, visual-description WebSearch; write a sidecar where a CC source is found; **flag untraceable photos** (do not fabricate provenance).
- **Export gate:** `export_bundle` (display posture, recon outputs present) raises `ReferenceProvenanceError` if any recon input photo lacks a cleared CC sidecar. This is the backstop — untraceable photos block the recon subset from shipping until swapped or user-provided.

### 4. Two-posture export gate (`app/public_export.py`, `scripts/export_public.py`, `scripts/build_dataset_release.py`)

`export_bundle(..., posture: Literal["display","redistribute"])`:

- **`redistribute`** (used by `build_dataset_release`): current strict behavior — `check_licenses` (normalized) fail-loud; **exclude commercial-model recon** (source `api:*`/`recon:*`/`frontier:*`) entirely; only normalized-CC/CC0/own ship. Carry `Admissibility` verdicts by applying `non_admitted_output_ids` at export (exclude gated outputs — the missing-gate fix).
- **`display`** (the public arena bundle): include display-cleared outputs = the redistribute set **plus** commercial-model recon whose model license permits display (per audit: TRELLIS/TripoSR MIT, Hunyuan2, Rodin, Hunyuan3.1/Tripo via fal commercial badge) — **with** (a) machine-generated label surfaced, (b) attribution carried, (c) reference-photo provenance enforced (unit 3). Still excludes the hard-18 (Xfrog/Demeter/AgriGen) and admissibility-gated outputs.
- **No bulk download in `display`:** no download button in the arena UI; `build_dataset_release` (the bulk tarball) only runs `redistribute`. Honest caveat documented in the DATASHEET: a web viewer must fetch the GLB to render, so display is not DRM — the legally-meaningful line is "no bulk downloadable dataset of commercial-model outputs."
- `EXPORT_MODELS` unchanged for `redistribute`; attribution travels via existing `ModelOutput.attribution` (already exported).

## Data flow

DB copy → `backfill_licenses.py` (correct/assign license+attribution, resolve Objaverse) → `source_reference_sidecars.py` (populate/flag recon photo provenance) → `export_bundle(posture=…)` → fail-loud on any un-cleared asset, admissibility-gated, licenses normalized → arena bundle (display) or dataset tarball (redistribute).

## Error handling

Every gate is fail-loud: `LicenseError` (unchanged), new `ReferenceProvenanceError`, and the admissibility exclusion is deterministic. No silent drops or includes. The backfill is idempotent and never mutates the real study DB.

## Testing

1. `normalize_license` — table-driven over every real DB label form → correct SPDX; NC/ND forms stay non-allowlisted; None→None.
2. `check_licenses` with normalization — space-form CC passes; NC/ND/null non-own fails loud.
3. `backfill_licenses` — own/LLM/procedural→CC0; crops3d→CC0; Objaverse nc/nd left non-allowlisted; Xfrog/Demeter/AgriGen untouched; idempotent (second run no-ops).
4. Reference provenance — all-taxa test; export raises `ReferenceProvenanceError` when a recon photo sidecar is missing/non-CC.
5. Two-posture export — `redistribute` excludes commercial-model + admissibility-gated + Tier-1; `display` includes display-cleared commercial-model with attribution + machine-gen label, still excludes Tier-1 + gated; both fail loud on an un-normalizable/unlicensed asset.
6. Admissibility carried into export — a non-admitted output never appears in either bundle.

## Out of scope (stays with user / counsel)

Legal sign-offs (crops3d data-record blessing, CC-BY-vs-CC0 confirmation, fal-grant-covers-redistribution for Hunyuan3.1/Tripo IF ever added to a dataset, Gemini API tier, PlantDreamer dataset license); the actual hosting/secrets; regenerating recon from swapped reference photos (only if the reverse-image-search sourcing fails and you choose to swap).

## Success criteria

`build_dataset_release` (redistribute) ships only normalized-CC/CC0/own assets — never a commercial-model mesh, an NC/ND asset, an admissibility-gated output, or a recon output whose input photo lacks a cleared CC sidecar — and fails loud if asked to. The arena bundle (display) additionally carries the commercial-model recon with attribution + machine-generated labels and no bulk-download surface. The 18 hard-excludes never appear in either. Full suite green.
