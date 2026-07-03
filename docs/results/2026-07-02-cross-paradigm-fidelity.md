# Cross-Paradigm Biological Fidelity: preference winners lose on completeness

**Date:** 2026-07-02 · **Board:** `/fidelity` (`/api/fidelity.json`) · **Aggregation:** `app/fidelity.py::fidelity_scorecard`

## Question

The preference arena ranks generators **only within a paradigm** — pairwise preference is not
commensurable across paradigms (a raw scan vs a procedural mesh vs a single-image reconstruction is
apples-to-oranges, and "a raw scan beats every generator" would be the marquee non-result). But
fidelity measured against the _same_ per-taxon ground truth **is** commensurable across paradigms.
So we can ask the question the preference arena structurally cannot: **which paradigm best
reconstructs each taxon on biological fidelity?**

## Method

Primary axis = **organism-level completeness** (the one κ-validated cross-paradigm signal: binary
complete-vs-incomplete κ=0.64; the 4-way category is experimental at 0.42). Each output's rendered
turntable views get a VLM organ-presence read against the taxon's expected-organ inventory
(`scripts/score_completeness.py`); we report `%complete` (share of a paradigm's outputs scored
`complete`) and the mean required-organ fraction. Scored **219 outputs across all 6 paradigms and 6
taxa**. The board is deliberately **multi-axis with no single blended score** — geometry (Chamfer
F-score) and trait fidelity are shown as labeled context, per SP4's "geometry is not enough."

## Finding

**The paradigms that win the preference arena on visual quality are the worst on biological
completeness.** Single-image reconstruction (`image_recon` — Hunyuan3D / TRELLIS, the perceptual
favorites) ranks **4th–6th of 6 on completeness in every taxon** (22–50% complete), because
single-view reconstruction systematically misses occluded and back-facing organs. The paradigms that
build a whole plant by construction — procedural (expert + LLM), text→3D, agentic — hit ~100%
completeness. Perceptual/geometric quality and biological completeness are **distinct, sometimes
anti-correlated axes**; this generalizes SP4's within-corpus "geometry is a weak proxy" (Chamfer
agreed with human trait judgments on only 8/31) across the full paradigm spectrum.

### Per-taxon completeness ranking

| Taxon                | Completeness ranking (paradigm, %complete, n)                                                                                              | image_recon rank |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------- |
| Arabidopsis thaliana | 1. proc_expert 100%(n1) · 2. proc_llm 100%(n3) · 3. text_native 100%(n5) · 4. agentic 100%(n2) · 5. retrieval 33%(n3) · 6. recon 22%(n9)   | **6/6**          |
| Glycine max          | 1. proc_expert 100%(n4) · 2. text_native 100%(n6) · 3. agentic 100%(n2) · 4. recon 88%(n8) · 5. proc_llm 80%(n5) · 6. retrieval 0%(n2)     | **4/6**          |
| Pinus sylvestris     | 1. proc_expert 100%(n1) · 2. retrieval 100%(n4) · 3. text_native 100%(n6) · 4. recon 35%(n17) · 5. proc_llm 20%(n5) · 6. agentic 0%(n2)    | **4/6**          |
| Rosa                 | 1. proc_llm 100%(n3) · 2. text_native 100%(n5) · 3. agentic 100%(n2) · 4. recon 88%(n8) · 5. retrieval 60%(n5) · 6. proc_expert 60%(n5)    | **4/6**          |
| Solanum lycopersicum | 1. proc_expert 100%(n4) · 2. proc_llm 100%(n3) · 3. text_native 100%(n5) · 4. agentic 100%(n2) · 5. recon 46%(n24) · 6. retrieval 27%(n26) | **5/6**          |
| Zea mays             | 1. retrieval 100%(n8) · 2. proc_expert 100%(n3) · 3. text_native 100%(n6) · 4. agentic 100%(n2) · 5. proc_llm 80%(n5) · 6. recon 50%(n18)  | **6/6**          |

(`proc_expert` = procedural_expert, `proc_llm` = procedural_llm, `recon` = image_recon.)

## Caveats (the board is honest about these)

- **Completeness is validated on the binary axis** (κ=0.64); the 4-way is experimental. Ranking uses
  `%complete` + mean required-organ fraction, not the 4-way category.
- **Multi-axis, no single ELO.** Geometry (Chamfer F-score, an SP4-weak proxy) is populated only
  where GT scans exist (`image_recon` / `retrieval` / `procedural_expert`); trait fidelity is
  currently unpopulated (all `botanical_accuracy` null) and renders as "—". Both are labeled by
  validation status on the board.
- **9/228 outputs** failed turntable render (heavy GLBs / model-viewer timeouts) and are uncounted.
- **`capture_scan`** (held-out reference scans) is shown as a GT upper-bound row, not a ranked
  competitor.
- Some cells have small `n` (e.g. n=1–3); treat per-taxon ranks as indicative, not significance-tested.

## Reproduce

- Board: `GET /fidelity` or `GET /api/fidelity.json`
- Scoring: `BIO3D_DATABASE_URL=…/data/study/arena-study.db BIO3D_DATA_DIR=…/bio3d-arena-mvp/data
python scripts/score_completeness.py`
- Aggregation: `app/fidelity.py::fidelity_scorecard(db)`

## Related

Builds on the "geometry is not enough" finding (the SP4 result: Chamfer a weak proxy for trait
fidelity) and on `docs/results/2026-07-01-completeness-validation-results.md` (the completeness
metric this board's primary axis reuses). Complements the within-paradigm preference boards. This is
the cross-paradigm biological-GT-fidelity seam vs 3DGen-Bench (preference/proxy, general objects,
generative-only).
