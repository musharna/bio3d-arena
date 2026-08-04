# Taxon3D — Plant Single-Image→3D Reconstruction Benchmark (v1 pilot results)

> **Status:** internal results report, 2026-06-21. Pilot (3 baselines), run on a local dev
> instance. The pre-registered methodology lives in the AgriGen tree
> (`docs/superpowers/specs/2026-06-20-bio3d-arena-benchmark-methodology.md`); this report is
> the post-hoc result against it. A formal/external write-up requires a citation audit first
> (deferred to the pre-release review).

## What this is

The first **living** leaderboard for **image→3D plant reconstruction accuracy vs ground truth**.
Per the benchmark-landscape analysis, this intersection — {3D-bio-recon × accuracy-vs-GT ×
living arena} — had no incumbent: the two live 3D arenas score _aesthetics_ only, and prior
plant-recon evaluations are static, single-paper tables. Taxon3D runs it as a **dual-mode**
benchmark:

- **Mode B (objective, this report):** each reconstruction scored against a **held-out, private**
  GT plant scan set — ICP-aligned unit-bbox chamfer, F-score@τ, coverage, plus a **GT
  natural-variation band** (is the recon within real conspecific shape spread, not just "small"?).
- **Mode A (perceptual):** anonymous pairwise votes → Bradley-Terry, with a vote↔metric
  **agreement** view. (No votes cast yet — see Limitations.)

## Setup

| Axis                  | v1 pilot                                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------- |
| Methods (baselines)   | TRELLIS, Hunyuan3D 2.0, InstantMesh (3 — below the ≥4 ideal; expansion roster pending)         |
| Species               | Arabidopsis thaliana, Solanum lycopersicum (tomato), Zea mays (maize), Pinus sylvestris (pine) |
| Recons per cell       | 5 (one per input photo) → per-method **error bars**                                            |
| Total reconstructions | 3 × 4 × 5 = **60**, all scored (0 errors)                                                      |
| Ground truth          | private held-out scans, ≤30 individuals/species; GT version `ec59de63…`                        |
| Pinned confounds      | `n_points=30000`, `tau=0.05`, `seed=0`, metric `unit_bbox_chamfer_icp_v1`                      |
| Leakage guard         | GT clouds never served; only input photos + this board are public                              |

Chamfer is the **mean over a method's 5 recons** (± population std). The **verdict** judges the
_typical_ recon (mean vs the band's P75), not the cherry-picked best.

## Results (chamfer ↓, mean ± std; PASS = typical recon within the GT natural-variation band)

**Arabidopsis thaliana** — band 0.116–0.141
| Method | n | chamfer | F-score | Verdict |
| --- | --- | --- | --- | --- |
| TRELLIS | 5 | 0.1062 ± 0.0239 | 0.261 | PASS |
| InstantMesh | 5 | 0.1066 ± 0.0233 | 0.279 | PASS |
| Hunyuan3D | 5 | 0.1214 ± 0.0178 | 0.230 | PASS |

**Solanum lycopersicum (tomato)** — band 0.042–0.049
| Method | n | chamfer | F-score | Verdict |
| --- | --- | --- | --- | --- |
| Hunyuan3D | 5 | 0.0601 ± 0.0085 | 0.511 | FAIL |
| InstantMesh | 5 | 0.0744 ± 0.0087 | 0.396 | FAIL |
| TRELLIS | 5 | 0.0895 ± 0.0244 | 0.296 | FAIL |

**Zea mays (maize)** — band 0.059–0.064
| Method | n | chamfer | F-score | Verdict |
| --- | --- | --- | --- | --- |
| Hunyuan3D | 5 | 0.0737 ± 0.0052 | 0.317 | FAIL |
| TRELLIS | 5 | 0.0844 ± 0.0231 | 0.333 | FAIL |
| InstantMesh | 5 | 0.1288 ± 0.0130 | 0.190 | FAIL |

**Pinus sylvestris (pine)** — band 0.023–0.026
| Method | n | chamfer | F-score | Verdict |
| --- | --- | --- | --- | --- |
| Hunyuan3D | 5 | 0.1121 ± 0.0223 | 0.203 | FAIL |
| InstantMesh | 5 | 0.1260 ± 0.0401 | 0.175 | FAIL |
| TRELLIS | 5 | 0.1305 ± 0.0493 | 0.196 | FAIL |

## Findings

1. **No single method wins.** Hunyuan3D has the lowest chamfer on tomato and maize; TRELLIS and
   InstantMesh are a statistical tie on arabidopsis (0.1062 ± 0.0239 vs 0.1066 ± 0.0233 — error
   bars overlap heavily); TRELLIS's best recon edges pine. A per-species, not per-method, story.
2. **Only arabidopsis is reconstructed within natural plant variation.** All three methods PASS
   the GT band for arabidopsis (a compact rosette), but **every method FAILs on tomato, maize, and
   pine** — even the best single-image→3D reconstruction is geometrically distinguishable from a
   real plant for the structurally complex species (broad leaves, tall stalks, conifer needles).
   This is the band channel's whole point: "small chamfer" ≠ "within real variation." The tight GT
   bands for tomato/maize/pine (0.04–0.06) sit below every method's mean.
3. **Error bars change the reading.** TRELLIS is high-variance (±0.024–0.049): on pine its mean is
   worst but its best recon is competitive. Hunyuan3D is the most consistent (±0.005–0.022). A
   point-estimate (v1, 1 recon/species) would have hidden this; n=5 surfaces it.
4. **InstantMesh is mid-pack and coarse.** Competitive on arabidopsis, weak on maize/pine, and its
   meshes are far smaller (e.g. a maize GLB ~0.35 MB vs TRELLIS ~40 MB) — a lighter, lower-detail
   reconstruction.

## Limitations (honest)

- **Pilot — 3 baselines**, below the ≥4 the fairness criterion wants; One2345++ / Direct3D /
  Unique3D are roster expansions pending an AgriGen harvest.
- **Mode A (votes) not yet collected** — the perceptual ranking + the vote↔metric agreement view
  are wired and ready but empty; no human/VLM votes have been cast.
- **Coverage is scale-dependent** (arbitrary glTF scale) → report-only; chamfer + F-score (unit-bbox,
  scale-invariant) are the ranking signals.
- **GT bands from ≤30 individuals/species** (and pine n is small) — bands are estimates.
- **Dev-local**: the board lives in an ephemeral local DB (the test suite wipes it); not yet hosted.

## Reproducibility

Re-run from the bake-off GLBs against the AgriGen scoring service:

```
# 1. scorer (AgriGen side):
cd ~/agrigen/backend && AGRIGEN_GT_BUNDLE=data/gt_bundle_prod \
  .venv/bin/uvicorn agrigen.scoring_service.app:app --port 8077
# 2. ingest + score (bio3d-arena):
BIO3D_RECON_SCORER_URL=http://127.0.0.1:8077 python scripts/ingest_bakeoff.py \
  --bakeoff-dir ~/agrigen/backend/data/bakeoff_p1 --no-rescore
BIO3D_RECON_SCORER_URL=http://127.0.0.1:8077 python scripts/ingest_bakeoff.py \
  --bakeoff-dir ~/agrigen/backend/data/bakeoff_p2
```

Every score row records its confounds + GT version hash. Metric code: `agrigen.eval.score_api`
(`unit_bbox_chamfer_icp_v1`); board code: `app/recon_service.py`.

## What's next

- **Synthetic-plant botanical fidelity** — the fast-follow vertical (reuses this Mode-A/Mode-B
  plumbing).
- **Collect Mode-A votes** — populate the perceptual ranking + agreement view.
- **Deploy** — host the app + scorer + a persistent DB so the leaderboard is actually living/public.
- **Roster to ≥4 methods** + **multi-view condition** — gated on AgriGen harvest.
