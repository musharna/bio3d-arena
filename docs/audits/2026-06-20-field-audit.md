# Bio 3D Arena — Field Audit (2026-06-20)

**Trigger:** User asked, after the submission/moderation queue shipped — "audit the field: what do
similar websites have that we lack? what improvements are we missing? how does our stuff look
visually? are we missing any bio benchmarks?"

**Goal:** Find concrete gaps vs (a) comparable arena/leaderboard platforms, (b) the bio-3D
benchmark landscape, and (c) modern UI/UX — to prioritize the next development increments.

**Method:** Three parallel research subagents (opus): arena-platform feature gap (WebSearch over
LMArena/GenAI-Arena/3D Arena/etc.), bio-3D benchmark gap (WebSearch over CASP/PDB/MedShapeNet/
Infinigen/etc.), and a fresh adversarial visual critic reading our actual templates/CSS.

**Caveat — visual:** No headless browser was available in the audit sandbox. The visual critique is
a code-level read of `app/templates/*` + `app/static/style.css` and reasoning about render
behavior; it could NOT capture rendered pixels, measure live contrast, or verify that the
model-viewer / 3Dmol.js viewers actually render in-browser. Treat visual findings as structural,
not pixel-confirmed. **Open action:** get a headless browser (playwright) to capture real
screenshots + verify 3D viewer runtime before the visual fixes are called done.

**Status legend:** `[ ]` open · `[~]` in progress · `[x]` done. Update inline as we act.

---

## A. Direct prior art (read before building)

- **3D Arena** (3darena.art, arXiv 2506.18787) — closest existing platform: open image-to-3D eval
  via pairwise human voting, 123k+ votes since 2024-06. Mine its methodology + prompt set. Key
  finding: voters bias toward Gaussian splats (+16.6 Elo) and textured models (+144 Elo) — a
  **representation/presentation confound** we must control for.
- **GenAI-Arena** (arXiv 2406.04485), **3DGen-Bench** (arXiv 2503.21745), **LMArena/Chatbot Arena**
  (arXiv 2403.04132), **imgsys**, **Artificial Analysis**. Methodology references:
  Leaderboard Illusion (2504.20879), Vote Rigging (2501.17858), Prompt-to-Leaderboard (2502.14855).

---

## B. Arena-platform feature gaps (what comparable sites have that we lack)

### B1. Statistical methodology & leaderboard rigor

- [x] **[HIGH] CI-grouped "Rank (Upper Bound)"** — models with overlapping 95% CIs share a rank;
      `rank = 1 + count(models whose lower CI > this model's upper CI)`. Most-copied gold-standard
      pattern (LMArena, Scale SEAL, Vals AI). _Low effort; we already have BT lower/upper._
- [x] **[HIGH] Per-row CI + vote-count columns visible on the leaderboard.** We compute BT CIs but
      the visible credibility layer (CI bar + n_games per row) needs surfacing. _Low._
- [x] **[HIGH] Explicit tie handling in the BT fit** — standard recipe duplicates each tie 50/50
      into A-win + B-win. **Verify ties aren't silently dropped today.** _Low._
- [ ] **[MED] Per-category / per-dimension leaderboards from one vote stream** (per molecule class:
      protein / nucleic-acid / complex / small-molecule, and per prompt-type). _Med._
- [ ] **[MED] Multi-dimensional voting** — separate Elos per criterion (structural correctness vs
      visual quality vs prompt-organism alignment). 3DGen-Arena votes 5 dimensions. Largely unexploited
      in bio. _Med._
- [ ] **[MED] Style/representation control in the BT regression** — regress out representation
      (cartoon vs surface vs sticks) + texture so prettier-but-wrong structures don't win. Directly
      counters the 3D-Arena +144-Elo texture confound. _Med._
- [ ] **[LOW] Active/adaptive matchup sampling** — sample pairs to maximize CI-width reduction
      (~35–54% fewer votes to significance). Valuable since bio-3D gens are expensive. _Med._

### B2. Voting UX

- [ ] **[HIGH] Minimum-engagement gate before vote unlocks** — require N seconds of orbit / min
      camera moves on BOTH viewers before enabling the vote. Stops drive-by voting on media that needs
      inspection (Artificial Analysis Video Arena pattern). _Low — JS event counter._
- [x] **Four-button vote (A / B / Tie / Both-bad)** — ALREADY HAVE (arena.html vote bar). _(verify
      the "Both bad" path feeds the BT fit correctly.)_
- [ ] **[MED] Void-on-reveal / blind-then-reveal enforcement** — audit served payloads; strip
      identifying fields (filenames, metadata, headers) during the blind phase, reveal only post-vote.
      _Low._
- [ ] **[MED] Synchronized cross-pane orbit (NOVEL — nobody has it)** — one drag rotates both
      models together for apples-to-apples structural comparison; powerful for aligned molecules.
      _Med — shared camera-state event bus._
- [ ] **[MED] Structure-only / wireframe / cartoon toggle in the voting viewer** — judge geometry
      separate from shading (3Dmol.js gives cartoon/surface/sticks/wireframe free). _Low._
- [x] **Keyboard shortcuts for voting** — ALREADY HAVE (arena.js ←/→/t/x); but UNDOCUMENTED in UI
      (see visual punch-list).
- [ ] **[LOW] Past-generations gallery / "surprise me" prompt.** _Low-med._

### B3. Domain moat (unique to biology — no general arena has this)

- [ ] **[HIGH] CASP-style structure-validation badges / a separate "is it physically valid?"
      track**, distinct from the aesthetic vote. Objective automatable backstops: RMSD / TM-score /
      GDT-TS vs reference, lDDT/pLDDT, reference-free stereochemistry (clashscore, Ramachandran
      outliers — MolProbity-style). **Our strongest differentiator** and a direct counter to the
      presentation confound. _Med-high — wire TM-align / MolProbity as a batch annotation step._

### B4. Transparency

- [ ] **[HIGH] Public anonymized vote-data release + reproducible notebook** that recomputes the
      leaderboard from the dump (LMArena/GenAI-Arena/imgsys/3D Arena all do this; AA's lack is its trust
      gap). _Low — periodic CSV/Parquet export + a Colab/notebook._
- [x] **Dedicated methodology page** — ALREADY HAVE (/methodology). Strong; could add a pipeline
      diagram (see visual).
- [ ] **[LOW] Changelog / dated leaderboard snapshots** (enables rank-over-time). _Low._

### B5. Community & API

- [ ] **[MED] Read API / JSON data endpoint** for leaderboard + rankings (LLM-Stats differentiator).
      _Low — one FastAPI route._
- [ ] **[MED] Model metadata cards** — provider, license, release date, modality/format (GLB vs
      PDB/mmCIF), generation cost/GPU-seconds, params. _Low-med — schema fields + detail page._
- [ ] **[MED] Head-to-head compare tool (pick 2–N models)** — direct Elo/CI/win-rate comparison.
      _Low-med._
- [ ] **[LOW] Self-serve model submission via PR/dataset** (vs only the moderation queue). _Med._

### B6. Engagement (industry-wide white space)

- [ ] **[HIGH] Embeddable "#N on Bio 3D Arena" badge + iframe rank widget** — absent on EVERY
      platform surveyed. Wide-open differentiation + viral distribution lever. _Low — dynamic SVG/PNG
      badge endpoint + iframe-able mini-leaderboard._
- [ ] **[MED] OpenGraph / social cards** for leaderboard + per-model pages. _Low._
- [ ] **[LOW] Per-session voter stats / vote streak.** _Low._
- [ ] **[LOW] Rank-over-time trend chart per model** (needs dated snapshots). _Low-med._

### B7. Trust / integrity (how the big arenas actually do it)

- [ ] **[MED] Anomalous-voter statistical detection** — binomial test of each user's votes vs
      community consensus (3D Arena flags at p<1e-5), or Fisher-combination + Bonferroni over a user's
      sequential p-values (LMArena, ~90% TPR). Complements our gold checks. _Med — batch job._
- [ ] **[MED] reCAPTCHA v3 + per-IP vote caps + prompt/vote dedup** (LMArena's actual stack). We
      have rate limiting + a captcha _seam_; add real CAPTCHA + dedup hash on prompt+pair. _Low-med._
- [ ] **[MED] Model self-identification leakage defense** (arXiv 2501.17858) — strip identifying
      metadata from served 3D files; add same-org / position-bias covariates to the BT regression.
      _Med._
- [ ] **[LOW] OAuth one-verified-ID-per-vote lane** (3D Arena uses HF OAuth). _Low-med._

---

## C. Bio-3D benchmark gaps (tasks + representations we're missing)

We currently seed only demo tasks with procedural assets across 5 mesh categories + proteins, and
render only **GLB/GLTF (mesh)** and **PDB/mmCIF (molecular)**.

### C1. Cleanest launch set — REAL benchmarks, ZERO new representation work

- [ ] **[HIGH] PDB/mmCIF, display today:** CASP16 monomers + complexes, CASP15/16 RNA & nucleic-acid,
      CAMEO archive, RCSB PDB (CC0), AlphaFold DB vs experimental PDB (CC-BY — needs attribution),
      RNA-Puzzles decoy sets (turnkey multi-model-vs-truth), SAbDab antibodies.
- [ ] **[HIGH] Mesh-native, display today:** HuBMAP HRA (65 reference organs, native GLB, CC-BY —
      drop-in for organs), NIH 3D (STL/X3D, Public Domain), MedShapeNet (~100k STL, mixed per-source CC
      — verify), **Infinigen procedural flora (BSD-3)** — strongest seed for plants/flowers/crops,
      TreeNet3D / Tree-D Fusion (trees).
- [ ] **[HIGH] AI side of pairings:** TRELLIS / TRELLIS.2 (text/image→3D, exports GLB) to generate
      the model-output side of plant/organism/anatomy tasks; Shap-E as a low baseline.

### C2. Benchmarks gated by license / conversion / expert judgment

- [~] **[MED] Docking / pose plausibility:** PoseBusters V2 (BSD-3), Astex Diverse, CrossDocked2020,
      DockGen — **needs SDF support + protein+ligand co-display.**
      _(SDF display path unblocked — Increment 1; content loading still needed.)_
- [~] **[MED] Conformer generation:** GEOM-Drugs / GEOM-QM9 — **needs SDF.**
      _(SDF display path unblocked — Increment 1; content loading still needed.)_
- [ ] **[MED] Protein/antibody design:** RFdiffusion binders (backbone-only — pre-pack), CDR-H3.
- [ ] **[MED] Roots:** CPlantBox (GPL-3.0 — license-check assets) — skeleton→tube-mesh needed.
- [ ] **[MED] Cells/organelles meshes:** Allen Cell shape meshes (VTK→GLB), OpenOrganelle/COSEM
      (CC-BY, meshes available), BodyParts3D (CC-BY-SA), cardiac digital-twin surfaces.
- [ ] **[MED] Fungi:** photogrammetry scans (Sketchfab/Artec, per-asset CC) — **the only systematic
      mesh-native fungal ground truth; biggest CONTENT gap.**

### C3. Representation / format gaps (3D data types we CANNOT render today)

- [x] **[HIGH] Native SDF/MOL2** — unlocks EVERY conformer/docking/SBDD task (GEOM, CrossDocked,
      PoseBusters, EDM outputs). 3Dmol.js/NGL/Mol\* parse SDF directly; do NOT convert SDF→PDB (drops
      bond orders/stereo). **Highest-leverage single engineering add on the molecular branch.**
      _(Done: SDF/MOL ingest + validation + 3Dmol.js viewer + seed demo + bundled heme reference — Increment 1 Tasks 1–6.)_
- [ ] **[HIGH] Voxel → marching-cubes → GLB ingest pipeline** — unlocks the LARGEST corpus
      available: CellMap, MitoEM, OpenOrganelle, TotalSegmentator (CC-BY), Medical Segmentation
      Decathlon, AbdomenAtlas (NC), AMOS/FLARE, ROSE-X. Formats: NIfTI, OME-Zarr, HDF5, OME-TIFF, MRC/
      CCP4, DICOM. **Highest-leverage single add on the mesh branch.**
- [ ] **[MED] Point-cloud → surface (Poisson/ball-pivot)** — unlocks real plant-phenotyping scans
      (Pheno4D, Crops3D, MaizeField3D, PLANesT-3D) — the highest-fidelity REAL plant 3D data, all
      point-cloud-native. Or add a native point-cloud viewer.
- [ ] **[MED] XYZ connectivity perception** (RDKit/OpenBabel) for QM9/EDM raw outputs (no bonds).
- [ ] **[LOW] Gaussian splat viewer** — 3D Arena shows voters PREFER splats, so excluding them
      biases the arena; relevant once we ingest image-to-3D outputs.
- [ ] **[LOW] PAE confidence overlay** (AlphaFold/RhoFold) — separate JSON matrix, needs a 2D
      heatmap component. (pLDDT in B-factor we CAN already color.)

**License watch:** CC0 (commercial-clean): PDB/CASP/CAMEO. CC-BY (attribution): HuBMAP HRA,
TotalSegmentator, AlphaFold DB. Public Domain: NIH 3D. BSD-3: Infinigen, PoseBusters. ShareAlike:
BodyParts3D, MSD. Non-commercial: AbdomenAtlas 3.0. GPL-3.0: CPlantBox. Per-source (verify):
MedShapeNet, MorphoSource. Registration-gated (link, don't mirror): PDBBind/CASF.

---

## D. Visual / UX punch-list (code-level critique — see caveat above)

**Overall verdict:** Coherent dark-theme MVP; the BT/bootstrap/bias-audit surface is more rigorous
than most public arenas. But chrome reads "developer-built, never design-reviewed." The 3D viewers —
the core product — ship with ZERO affordances. ~1 focused day on viewer affordances + matrix legend

- accessibility moves it from "prototype" to "credible."

### D1. 3D viewer UX (core product — weakest area)

- [ ] **[CRITICAL] No interaction hint on viewers** — bare gradient box; add a "drag to rotate ·
      scroll to zoom" chip in each `.viewer-slot` (pointer-events:none, fades on first interaction).
- [ ] **[CRITICAL] No loading state** — `mountMesh`/`mountMolecular` show nothing while a large GLB/
      PDB loads. Inject spinner; remove on model-viewer `load` event / after `viewer.render()`.
- [ ] **[CRITICAL] No asset-failure fallback** — molecular `fetch` has no try/catch; a 404 leaves a
      blank slot next to a working one → silently biases the vote. Wrap both mounts, render "⚠ Failed to
      load" on error.
- [ ] **[HIGH] No reset-camera / fullscreen control** — 360px is small; users can't recenter after
      zoom. Add a per-`.model-col` control strip.
- [ ] **[HIGH] model-viewer missing `alt` / `poster` / `loading`** (accessibility + perceived load).
- [ ] **[MED] Molecular bg `0x131a24` (flat) mismatches slot radial gradient** — the two columns
      won't look like peers. Make both transparent or both flat.
- [ ] **[MED] Molecular representation locked** (stick+sphere+cartoon) — add a representation
      dropdown; structural-bio voters will want cartoon/surface/ball-and-stick.

### D2. Arena voting page

- [ ] **[HIGH] Vote bar visually flat** — 4 buttons share `--panel2`, differentiated only by a 1px
      border (nearly invisible on dark). The app's primary action has almost no visual weight. Fill the
      A/B win buttons with accent; make Tie/Both-bad clearly secondary; reduce emoji.
- [ ] **[HIGH] Keyboard shortcuts undocumented** — bind exists (arena.js:117) but UI never shows it;
      add `<kbd>` badges + a legend.
- [ ] **[MED] No per-button vote-registered feedback** before next pair loads (only a 700ms status
      flash). Highlight the chosen button.
- [ ] **[MED] `.viewer-slot` fixed 360px doesn't shrink on mobile** — stacked viewers push vote
      buttons below the fold. Use `clamp(220px,45vh,360px)`; consider a sticky vote bar.
- [ ] **[MED] Initial load shows literal `…` placeholder** — add skeleton shimmer.

### D3. Significance matrix & leaderboard

- [ ] **[CRITICAL] Significance matrix NOT colorblind-safe** — red↔green coded, number is the only
      redundant cue (and it's a probability, not good/bad). Add a non-color cue (▲/▼ arrows or diverging
      blue↔orange) AND a visible legend.
- [ ] **[HIGH] Matrix has no legend at all** — render a swatch key above the table.
- [ ] **[MED] Matrix doesn't scale past ~8–10 generators** — add sticky header + first column,
      truncate/rotate long labels.
- [ ] **[MED] Leaderboard BT-score column has no visual encoding** — add a horizontal CI whisker
      bar so ties are obvious at a glance.

### D4. States, accessibility, polish

- [ ] **[HIGH] Empty states leak template fragments** — significance bias-audit table renders raw
      `{{ bias.* }}` / `—` when `sig.status != 'ok'`. Gate the whole bias block behind a data check.
- [ ] **[HIGH] No visible focus styles anywhere** — add `:focus-visible{outline:2px solid
var(--accent2)}`; the app advertises keyboard voting but gives no focus indication.
- [ ] **[HIGH] `--muted #8b98a9` on `--bg #0f1419` ≈ 4.0:1 — fails WCAG AA** for body text (used in
      `.subtle`, `.criterion`, nav, prompts). Lighten to ~#9fb0c3 (≈5:1) or reserve for large text.
- [ ] **[HIGH] No favicon** — blank tab icon signals "unfinished." Add SVG + apple-touch-icon.
- [ ] **[HIGH] Admin link in PUBLIC top nav** (base.html:27) — remove Admin/Moderation from public
      nav even if token-gated server-side.
- [ ] **[HIGH] "MVP" stamped in every footer** (base.html:32) + "Upload Model Output" admin headings
      — undercuts the rigorous methodology; a researcher reads "MVP" as "don't cite." Drop from public
      chrome.
- [ ] **[MED] No `prefers-reduced-motion` block** (becomes mandatory once a spinner is added).
- [ ] **[MED] Status updates not announced** — add `aria-live="polite"` to `#status-line` /
      `#submit-status`.
- [ ] **[MED] 3D viewers fully inaccessible to screen readers** — add `aria-label`.
- [ ] **[MED] No active-nav indication / no responsive nav** — 7-item nav wraps on phones; add
      `aria-current` style + a collapse under ~600px.
- [ ] **[MED] Raw exception strings dumped into UI** (`"Error: " + err`) — friendly error component
  - retry; log raw to console only.
- [ ] **[LOW] CDN scripts (googleapis + jsdelivr) lack SRI/fallback** — viewers break silently if a
      CDN is blocked. Add `integrity`/`crossorigin` + fallback check.
- [ ] **[LOW] System-font stack only / no spacing scale / emoji brand** — add one webfont (Inter),
      `--space-*` tokens, an SVG logomark.
- [ ] **[LOW] Methodology page text-only** — add an SVG pipeline diagram (vote→Elo/BT→CI→sig).

---

## E. Recommended next increments (synthesis)

Ordered by leverage-per-effort. Each is one shippable increment in the established
test → live-verify → commit → merge pattern.

1. **Real benchmark content + SDF support** (C1 + C3 SDF) — the platform is empty without real tasks;
   SDF unlocks the entire molecular-docking world. Highest product value.
2. **Leaderboard credibility surface** (B1 CI-rank + CI bars + vote counts + tie handling) — low
   effort, directly raises citability; verify ties aren't dropped.
3. **Viewer affordances + accessibility pass** (D1 + D4 focus/contrast/favicon/MVP/admin-nav) — the
   ~1-day fix that moves the _whole site_ from prototype to credible.
4. **Domain moat: structure-validation track** (B3) — our strongest differentiator vs general arenas.
5. **Transparency: vote-data export + read API** (B4 + B5) — cheap, high trust ROI.
6. **Engagement: embeddable rank badge** (B6) — unique white space, viral lever.
7. **Voxel→GLB pipeline** (C3) — unlocks the largest corpus; bigger build, schedule after 1–4.

**Get a headless browser (playwright) first** so the D-series visual fixes can be screenshot-verified
rather than reasoned-about.
