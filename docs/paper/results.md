# Results (SP4 paper — draft): geometry is not enough

> Draft results section for the P-A paper, computed 2026-07-01 on the internal study data
> (`data/study/arena-study.db` + the human trait-calibration labels). Numbers are reproducible via
> `scripts/`-style analysis over 397 human trait-labelings, 175 Chamfer-scored outputs, 138 votes.
> The reference-free completeness section (2026-07-05) adds 39 fungal-kingdom outputs scored with
> the organism-completeness metric (`scripts/score_completeness.py`); a second fungal wave is in
> flight and will extend it.
> **Scale caveat:** this is the internal evaluation phase — small n, single calibration round; all
> figures are provisional and reported with that framing.

## The headline: a third of trait judgments cannot be made

Across **397** literature-trait × generated-output labelings (6 taxa), a human annotator returned:

| Verdict            | n       | %         |
| ------------------ | ------- | --------- |
| present_correct    | 158     | 39.8%     |
| **not_assessable** | **126** | **31.7%** |
| absent             | 110     | 27.7%     |
| present_wrong      | 3       | 0.8%      |

**Nearly one in three (31.7%) literature-trait judgments on generated 3D plants could not be made
at all** — the output or the trait did not admit a verdict. This is the central obstacle to
evaluating biological 3D generation, and it is invisible to any metric that assumes every output
is a scorable plant.

## Why judgments fail: morphological incompleteness dominates

Categorizing the free-text reason on each `not_assessable` label (keyword rules over the
annotator's note; 37% of not_assessable rows carried no note and are counted "unspecified", so the
incompleteness figure below is a **lower bound**):

| Reason                                                                                                        | n   | % of not_assessable |
| ------------------------------------------------------------------------------------------------------------- | --- | ------------------- |
| **output morphologically incomplete** ("not a plant / junk", "just sticks", "only fruit", "stub", "seedling") | 52  | 41.3%               |
| unspecified (no note)                                                                                         | 47  | 37.3%               |
| trait itself unjudgeable ("weird trait", "N/A trait", "what does this trait mean")                            | 16  | 12.7%               |
| other / uncertain                                                                                             | 7   | 5.6%                |
| too low-res / no color                                                                                        | 4   | 3.2%                |

The dominant failure is **the generated output not being a complete plant**: an isolated fruit, a
leafless stub, a seedling where a mature plant was asked for. At the output level, **33 of 135
distinct labeled outputs (24.4%) were flagged morphologically incomplete on at least one trait.**
A secondary failure is that a minority of literature-derived traits are not visually assessable
from a rendered model at all (12.7% of not_assessable) — a caution for anyone mining trait
ontologies for automatic scoring.

## Geometry (Chamfer) is a weak proxy for morphological completeness

Joining the incompleteness flags to held-out-scan Chamfer distance (lower = better; 175 outputs
scored):

- morphologically-incomplete-flagged outputs (n=31): **median Chamfer 0.099**
- all other scored outputs (n=144): **median Chamfer 0.076**

Incomplete outputs are only _modestly_ worse on geometry, and critically, **8 of 31 (26%)
morphologically-incomplete outputs score at or below the median Chamfer of the rest** — a quarter
of the outputs a human rejects as "not a plant" look _geometrically fine_. Chamfer distance to a
ground-truth scan therefore catches some, but misses a substantial fraction of, morphological
failure. This is the concrete case against ranking generative plant models by geometric distance
alone (cf. the static single-metric plant benchmarks), and the motivation for coupling objective
metrics with human + trait-grounded morphological evaluation.

## Reference-free completeness scales where geometry cannot — extending to a second kingdom

The previous section shows Chamfer is a _weak_ proxy where a ground-truth scan exists. But the
sharper problem is that **for most organisms a ground-truth mesh does not exist at all**. To test
whether the evaluation generalizes past the six calibration plants, we added a second kingdom —
Fungi (common puffball, lion's mane, and a Cucurbita fruit) — for which the arena holds **no
ground-truth mesh**. The consequence for geometry is absolute, not gradual: **0 of 24 fungal
reconstruction outputs receive a Chamfer score** (all 24 objective-metric rows resolve to
`status=error`, null distance). Any leaderboard ranked on geometric distance simply _omits these
taxa_ — the failure is silent, because a null is not a low score.

The organism-level **completeness** metric is reference-free by construction: a VLM reads a
turntable contact sheet of the generated model and checks each expected organ (from an authored
per-taxon organ inventory) as present / absent / uncertain — no GT required. It therefore covers
exactly the cells geometry leaves blank: **all 39 fungal outputs scored, 0 errors**, and the result
is a clean cross-paradigm split (score = required-organs-present / required; "complete" = whole
organism):

| taxon (tier)       | image→3D recon complete | text→3D complete |
| ------------------ | ----------------------- | ---------------- |
| puffball (easy)    | 8/8 (mean 1.00)         | 5/5 (1.00)       |
| lion's mane (hard) | 5/8 (0.63)              | 4/4 (1.00)       |
| Cucurbita (fruit)  | **1/8 (0.13)**          | 6/6 (1.00)       |

**text→3D produced a complete, recognizable body in 15/15 cases**, while single-image recon was
highly variable (14/24), degrading with both geometric difficulty (puffball 8/8 → lion's mane 5/8)
and — decisively — input quality.

**The metric doubles as a data-quality auditor.** The Cucurbita recons collapse (1/8 complete) not
because the generators failed but because the reference photo depicts the vegetative _plant_ (leaves,
vine, a blossom), not the _fruit_ the task asks to reconstruct; the recons faithfully rebuilt
foliage. The completeness metric's per-output notes surfaced this directly ("shows a vegetative
Cucurbita plant … no fruit body visible"), a diagnosis no geometric distance to a (nonexistent)
scan could have produced. This is the same reference-free signal catching a silent _input_ bug that
it was built to catch in _outputs_.

**Caveat (uncalibrated cross-kingdom extension).** The completeness metric was validated on plants
at binary κ=0.64; the fungal body-plan inventories (fruiting body required, surface features
optional) are authored, not yet human-calibrated. These fungal figures are therefore reported as
_reference-free coverage_ plus qualitative agreement (spot-checked against the VLM's own rationales),
not as a calibrated score. A second fungal wave (four further taxa: bolete, fly agaric, morel,
turkey tail) is in flight and will extend this table.

## Difficulty is strongly taxon-dependent

The not-assessable rate varies more than five-fold across taxa:

| Taxon                | not_assessable | rate      |
| -------------------- | -------------- | --------- |
| Pinus sylvestris     | 31/61          | **50.8%** |
| Glycine max          | 16/34          | 47.1%     |
| Rosa                 | 25/72          | 34.7%     |
| Solanum lycopersicum | 34/123         | 27.6%     |
| Zea mays             | 17/77          | 22.1%     |
| Arabidopsis thaliana | 3/30           | **10.0%** |

Architecturally complex taxa (pine, soybean) are far harder for current generators to render
assessably than the model organism (Arabidopsis). A single-species benchmark would badly
mis-estimate the state of the field; this motivates the multi-taxon, difficulty-stratified design.

## Limitations

- **Scale:** 397 labelings / 175 scored outputs / 138 votes, internal phase — provisional.
- **Single calibration round**, one annotator; inter-rater reliability not yet established (the
  chance-corrected VLM-judge κ is reported separately and is currently below the reliability gate —
  itself an honest finding about automatic scoring of plant morphology).
- The incompleteness categorization is keyword-based over free-text notes; the 37% unspecified
  bucket makes the 41% output-incompleteness figure a lower bound.
- The Chamfer comparison is over the intersection of trait-labeled and GT-scored outputs (n=31
  incomplete); it shows Chamfer is a _weak_ signal, not a useless one.
