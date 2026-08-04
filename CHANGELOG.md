# Changelog

Notable changes to Taxon3D, a multi-paradigm benchmark arena for biological 3D generation.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Entries are
milestone-level, not commit-level. For commit detail, see `git log`; most milestones also carry a
design spec under `docs/superpowers/specs/`.

## [Unreleased]

## [0.1.0] - 2026-08-04

First tagged release, and the first archived on Zenodo. It is cut here because the project's
name is now settled — a DOI freezes the name into the citation record, so the rename had to land
first. The benchmark itself is early: the leaderboard deliberately ranks nobody, because no
generator yet has enough comparisons to rank honestly.

### Changed

- **Renamed the project from "Bio 3D Arena" to "Taxon3D".** The old name collided twice and
  neither collision was survivable: `bio3d` resolves to the Bio3D R package for protein structure
  analysis (a 2006 _Bioinformatics_ paper with ~20 years of citations, in the same broad field),
  and "bio3d arena" resolves to Arena3D, a different bioinformatics visualisation tool. Searching
  the exact product name returned neither this project nor anything close to it, so word of mouth
  failed as well as search. Renamed now because the cost only rises: no DOI has been issued yet,
  and a DOI freezes the name into the citation record permanently.

  Deliberately **not** renamed: the `BIO3D_*` environment variables, the `bio3d_*` cookie and
  localStorage keys, the `b3d-` CSS prefix, and the `bio3d-arena` value stored in
  `model_output.source`. The last one is load-bearing data — `sourcing.py`, `spotlight.py`,
  `public_export.py` and `reference_provenance.py` all compare against it to decide whether an
  output is our own generation, so renaming it would silently mis-classify every existing row.
  The others are invisible to visitors and renaming them would either require an atomic secret
  migration or log every voter out, in both cases for no discovery benefit.

- Pinned the seven previously-unpinned dependencies (`pillow`, `scikit-image`, `scipy`, `nibabel`,
  `tifffile`, `anthropic`, `open_clip_torch`) to the versions the app is actually built and tested
  against. `open_clip_torch` had no constraint at all, so a fresh install could resolve any future
  major release.
- Corrected `requires-python` from `>=3.10` to `>=3.12`. The old floor was untested and
  unsatisfiable: pinned `scipy` and `tifffile` both require 3.12+, so no 3.10 or 3.11 environment
  could resolve the real dependency set. Ruff's `target-version` moved to `py312` to match.
- Pinned `ruff`, previously an undeclared dev dependency, and made the tree clean under it —
  `ruff check` goes from 11 errors to 0 and all 433 files are formatted. An unpinned formatter
  rewrites files differently per machine, and an unformatted tree means the next `ruff format`
  buries unrelated churn in someone's change. No `app/` code was touched.

- Unified the redistribution allowlist. "May we redistribute this?" is one question, asked of both
  a generated output and the reference photo a recon derives from, but it was written as two
  hand-maintained literals that drifted in **both** directions: the export copy admitted
  `CC-BY-2.0`, `PUBLIC-DOMAIN`, and `ODbL-1.0` that the reference copy rejected, while the
  reference copy admitted `CC-BY-SA-3.0` that the export copy rejected. It now lives once, in
  `app.licensing.REDISTRIBUTABLE_LICENSES`, imported by all four consumers. Net effect on real
  data: `gourd_ref.jpg` (Wikimedia, CC-BY-2.0) is now cleared, unblocking the 10 recon outputs
  derived from it; no output's redistributability changes, since nothing in the corpus is
  `CC-BY-SA-3.0`.

- Split dependencies into runtime / research / dev layers. `requirements.txt` had conflated what
  the web app needs to serve with offline corpus-building and scoring tooling, so deploying the
  public instance installed `open_clip_torch` → `torch` → the full NVIDIA CUDA stack onto a host
  that never imports it. Measured on a clean venv: **173 MB runtime-only vs 5.6 GB**. Verified by
  booting `uvicorn` against a runtime-only install with the research stack genuinely absent — every
  public route returns 200. `tests/test_runtime_deps.py` boots the real ASGI app in a subprocess
  and fails if serving ever imports the research stack.
- Fixed the deploy runbook, which had no dependency-install step at all, and which told the
  deployer to confirm `/benchmark` returns 200 — it is an internal page and correctly hard-404s
  under the public posture, so the documented smoke test failed against a correct instance.

### Removed

- Removed the `Cucurbita pepo` (pumpkin) task from the corpus. It is a plant fruit that had been
  filed under the Fungi kingdom and scored "complete" as a lone fruit — but a plant's organism is
  the whole plant, not one organ. It was the only such misfit; the six remaining Fungi taxa are all
  genuine fungal fruiting bodies (for which the fruiting body _is_ the whole macro-organism).

### Fixed

- Stray ground/floor planes in LLM-authored meshes. Some code-gen models (grok-4.20 in the
  commissioned paradigm, gpt-5-6-sol in the agentic paradigm) added a large horizontal plane the
  organism sits on, despite the prompt asking for a single whole specimen; it rendered as a slab
  through the subject in the turntable that both human voters and the VLM judge score. Added a
  world-space subject-isolation guard (`app.mesh_subject`) wired into `commission.run_bpy` — which
  both paradigms author their mesh through — and stripped the 25 affected meshes retroactively. A
  corpus-wide sweep of all 847 votable GLBs confirms none remain (and 0 leftover default cubes).
- A plant fruit could be scored a "complete organism". `Cucurbita pepo`'s completeness inventory
  used the fungal single-body model (`_body_inv`), so a lone fruit satisfied the sole required organ
  and scored complete. `_body_inv` is now fungi-only; plants use `_inv`, which requires a vegetative
  axis + foliage, so a lone fruit correctly reads as an isolated organ. An invariant test forbids
  any plants-kingdom taxon from single-body completeness scoring.

### Added

- This changelog, backfilled from git history.

---

## Milestones

### Licensing and ranking correctness (2026-07-19 → 2026-07-21)

- Reference-photo licence clearance is keyed by the **photograph**, not the taxon. Copyright attaches
  to an individual image, so a cleared photo no longer launders a different, unrecorded photo that
  merely shares a taxon prefix (#83).
- A cached judge rating can no longer outlive the fit that produced it (#82).
- Output ownership became a predicate instead of a repeated source literal (#81).
- The agentic generation runner starts from an empty scene — previously Blender's startup cube was
  exported as if it were a generated mesh (#80).
- Message Batches API path for the VLM judge, halving judge cost (#75).

### Roster expansion and harness honesty (2026-07-13 → 2026-07-18)

- Grew the agentic roster from 3 to 8 entrants across 9 labs, with vision as a hard constraint.
- Code-generation prompts and roster now derive from `ORGAN_INVENTORY`, so every kingdom is reachable.
- Commissioned-generation scoring measures the organism rather than our own plumbing: a crash is no
  longer recorded as an empty mesh, a billing failure is no longer a model failure, and a
  re-measurement is no longer counted as a second entrant.
- An attempt is modelled as a measurement under a harness, not as a fact about a model.
- Same-model re-hosts fold under a single leaderboard entrant.

### Leaderboard IA, sharing, and mobile (2026-07-10 → 2026-07-12)

- Modality-primary information architecture: hub, per-modality boards, head-to-head view, and a clear
  human/judge delineation (#61).
- A model can never be compared against itself — fixed across the arena, the judge fit, and the judge
  sampler, where self-matches were pumping strength (#62).
- Honest Open Graph share cards; a zero-vote model shows no rank (#63).
- Mobile voting pass — phone voters had been dead-ended after their first vote (#64).
- Per-IP rate limiting, opaque output-scoped asset URLs, and a provisional-rank badge.

### Design system v2 and kingdom filtering (2026-07-06 → 2026-07-09)

- OKLCH light/dark token system, sidebar app shell, and a 3-state Auto/Light/Dark theme.
- Kingdom became a global request-scoped filter (middleware + cookie) threading through the arena
  pool, matchmaking, leaderboard, significance, coverage, dataset, difficulty, and spotlight.
- Every public page rebuilt against the v2 prototype and certified at parity.
- Real per-kingdom point-cloud hero on the landing page.
- Internal research and analytics pages unpublished on the public instance, including the JSON
  endpoints backing them.

### Third kingdom: animals (2026-07-06)

- Generalized plant-specific machinery to organisms: the semantic gate is taxon-parameterized and
  admissibility-only, and the human flag reason became `not_the_organism`.
- Animal body-plan completeness via `Organ.complement`, with a complement-aware `derive()` and a new
  `malformed` category.
- Dog, mallard, monarch, and goldfish inventories, recon/text generation, and difficulty tiering.

### Reference-image integrity (2026-07-06)

- CLIP/BioCLIP capability module with a photo-domain feasibility probe. The probe killed the naive
  binary species-representativeness framing and established multi-class species ID (13/13).
- Gallery QA scoring with a `passed_qa` filter; recon input photos excluded from the vote UI.
- Semantic scoring renders each output in a timeout-guarded subprocess, so one wedged mesh cannot
  stall a batch.

### Publish safety and licensing posture (2026-07-04 → 2026-07-06)

- Two-posture export: `display` versus `redistribute`, with admissibility exclusion.
- Licence-string normalizer, licence backfill, and a fail-loud reference-photo provenance gate that
  fails closed on unidentifiable recon input.
- AI-generated labelling and attribution on display outputs, with no download affordance.
- Gold-pair outputs gated by their true underlying provenance rather than the decoy row's.

### Difficulty roster and fungi expansion (2026-07-04 → 2026-07-05)

- Multi-axis geometric-difficulty rubric across 7 taxa with cited axes, and a `TaxonDifficulty` side
  table as the per-taxon source of truth.
- Paradigm × tier objective scorecard.
- Second kingdom: 7 fungi taxa (puffball, gourd, lion's mane, bolete, fly agaric, morel, turkey tail).
- Reference-free completeness extended to fungal body plans.

### K-wise voting (2026-07-04)

- Simultaneous 4-up pick-best ballots: `KBallot`, `pick_quad` over the least-compared same-paradigm
  outputs, and a 4-up arena UI.
- Ballot-level bootstrap so derived pairs do not artificially tighten confidence intervals.

### Admissibility gating (2026-07-03)

- Pluggable pre-vote gate: a `Predicate` protocol with a composer, admitting only when all predicates
  pass.
- Structural predicate using conservative degeneracy checks, reading assets through the storage
  backend so it stays S3-safe.
- VLM cardinality/identity semantic predicate, shipped advisory and promoted to a hard gate once it
  earned it (0 real false positives; recall 11 → 13).
- Bad-output handling: auto-gate on completeness category, per-session human flag with auto-hide at
  K, and an admin moderation view.

### Self-improving generation and completeness metrics (2026-07-01 → 2026-07-02)

- **D-Complete**: organism-level completeness metric — per-taxon expected-organ inventory, VLM
  organ-presence scorer, category derivation, and a validation harness. Validated at binary
  kappa 0.64 (n=114).
- **D-Gen**: rubric-in-the-loop self-improving generation — render → critique → refine with plateau
  detection and best-round promotion. An independent blind cross-judge preferred the refined mesh
  5/8 with zero flips.
- Cross-paradigm biological-fidelity board.

### Multi-paradigm foundation (2026-07-01)

- `Generator.paradigm` with a fail-loud backfill classifier and a `same_paradigm` predicate.
- Matchmaking pairs only within a paradigm, and cross-paradigm comparisons were dropped from rating
  aggregation — they had been contaminating the Elo column.
- New paradigms: `text_native` (text → 3D across 6 taxa) and `agentic` (LLM render-critique-revise).
- Procedural code-generation scorecard (pass@1 plus morphology fidelity).

### Go-public instance, dataset release, and auth (2026-06-30 → 2026-07-01)

- Separate public instance: curated, licence-gated export with a fail-loud licence gate, a
  referentially-complete include-set, and a real-execution round-trip leak test.
- `SCORING_ENABLED` guard so the public instance never dials a scorer.
- Dataset release bundles with a datasheet and leak guard.
- Hugging Face OAuth login with a verified-only leaderboard scope.
- Terms, privacy, and licences pages generated from output provenance.

### Commissioned-generation arena (2026-06-30)

- N LLMs write Blender Python against a 6-taxa prompt set, executed in a sandboxed `bpy` runner with
  secrets scrubbed from the environment.
- Resumable batch orchestrator, GLB mesh-validity checking, and a CLI driver.

### Mode-C trait ground truth (2026-06-29 → 2026-06-30)

- VLM trait-checking core with authored, provenance-validated rubrics and per-class kappa calibration.
- Live rubric sourcing from Wikidata and Europe PMC, gated by `ghostcite` citation verification.
- Human-calibration CSV round-trip and browser labeller. Calibration came back negative (no class
  reached kappa 0.6), so Mode-C ships **experimental**.

### Evaluation loop and governance (2026-06-27 → 2026-06-29)

- VLM-as-judge evaluation loop with a bounded connected-pair sampler to densify per-tier ranking.
- Per-tier perceptual ranking to test whether the winner shifts across difficulty tiers.
- Plant input advisor: morphology → recipe plus a VLM photo grader.
- `/coverage` governance and per-model/per-task disclosure page.
- Six live audits covering data integrity, assets, client JS, admin auth, and write surfaces.
- The test suite hard-refuses to run against a non-throwaway database, after a run wiped the study DB.

### Foundation (2026-06-20 → 2026-06-26)

- Pairwise voting arena with an Elo/Bradley-Terry leaderboard.
- Research-grade evaluation: multi-criterion scoring, per-category slices, tie-aware BT, and export.
- Ingestion API and Python client for registering generator GLBs programmatically.
- Format-keyed viewer registry (model-viewer for meshes, 3Dmol.js for molecular formats).
- Paired-bootstrap pairwise significance testing and a bias audit.
- Vote integrity: gold attention checks, trust gating, rate limiting, and captcha.
- Scale-out seams: storage abstraction (local/S3), pooled engine, Redis rate limiting.
- Community submission with a moderation queue.
- Initial taxon coverage across maize, rose, soybean, arabidopsis, pine, and tomato, including
  image → 3D reconstruction, multi-view reconstruction, and procedural L-system sources.
