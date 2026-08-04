# Related Work (SP4 paper — draft)

> Draft related-work / positioning section for the Taxon3D paper (P-A). Citations are
> anchor-verified (abstracts read; arXiv/DOI checked) but NOT yet ghostcite-gated — run a full
> citation audit (byline + retraction) before submission. First-author surnames verified.

Taxon3D sits at the intersection of four lines of work: arena-style human evaluation of
generative models, VLM-as-judge evaluation for 3D, objective and perceptual evaluation of
generated _plant_ geometry, and the emerging critique of leaderboard governance. No prior system
spans them; we position against each in turn.

## Arena-style evaluation of generative 3D

Open, continuously-updated leaderboards built on anonymous pairwise human preference — the format
popularized by Chatbot Arena for language models — have recently reached generative 3D. **3D
Arena** (Ebert, arXiv:2506.18787) is the largest such effort, accumulating 123,243 votes from
8,096 users across 19 image-to-3D models. It is the methodological template and the scale
benchmark we measure against. It is, however, a _generic-object_ arena (evaluated on the iso3d
prompt set): it reports Elo without Bradley–Terry confidence intervals in its product framing,
uses no ground-truth geometry, and applies no calibrated automatic judge. Notably, 3D Arena
surfaces a _format confound_ — Gaussian-splat outputs enjoy a +16.6 Elo advantage over meshes and
textured models a +144.1 Elo advantage over untextured ones — which it flags as an open problem
and explicitly recommends "format-aware comparison" as future work. Our position/format bias audit
addresses exactly this confound and, unlike 3D Arena, _corrects_ for it rather than only reporting
it, in the biological setting the original leaves open.

**3DGen-Arena / 3DGen-Bench** (Zhang et al., arXiv:2503.21745) proposes an arena-style collection
platform for generative 3D and trains CLIP- and MLLM-based reward scorers from the collected
preferences; annotation is a hybrid of public and expert raters over generic-object prompts.
**GenAI-Arena** (Jiang et al., arXiv:2406.04485) applies Bradley–Terry estimation to ~9,000 votes
across image, image-editing, and video generation but does not cover 3D. **K-Sort Arena** (Li et
al., arXiv:2408.14468) improves the _statistical efficiency_ of preference collection via K-wise
comparisons — a complementary ranking-method advance orthogonal to domain. All share our arena
mechanics but none targets a scientific domain, and none couples human preference to a
domain-grounded correctness signal.

## VLM-as-judge for 3D

Using a vision-language model as a human-aligned evaluator was established for text-to-3D by
**GPTEval3D** (Wu et al., arXiv:2401.04092), which elicits GPT-4V pairwise judgments and converts
them to Elo on generic objects. We adopt the VLM-judge paradigm but treat _judge validity_ as a
first-class question: we report chance-corrected inter-rater agreement (Cohen's κ) between the
judge and human voters, with both-order presentation and forced-tool decoding, rather than
assuming alignment. This matters acutely in a scientific domain, where a fluent-but-wrong judgment
is more dangerous than an obvious one; in our setting the calibration is honest about where the
judge does _not_ yet agree with humans.

## Evaluation of generated plant geometry

The closest work by _domain_ is recent single-image-to-3D reconstruction of plants. **PlantDreamer**
(Hartley et al., arXiv:2505.15528, ICCVW 2025) is a generation method (diffusion-guided Gaussian
splatting from L-system meshes and real point clouds), and the accompanying **Plant Methods**
benchmark (Gao, Hartley & French, doi:10.1186/s13007-025-01482-6, 2026) evaluates six image-to-3D
generators (Hunyuan3D-2.0, TRELLIS, One-2-3-45++, InstantMesh, Direct3D, Unique3D) on bean, kale,
and mint against ground-truth scans using Chamfer distance, F-score, normal consistency, and
perceptual metrics. These establish that objective geometric evaluation of generated plants is
feasible and valuable — but they are _static, single-species, offline_ studies with no human
preference signal, no live or continuously-updated leaderboard, no calibrated judge, and no bias
auditing, and they rank methods by geometric distance to a single reference (implicitly treating
geometry as quality). Adjacent plant-3D datasets (ROSE-X, Pheno4D, Crops3D) target phenotyping and
segmentation rather than generation. Taxon3D adds the live human + calibrated-VLM preference
layer, statistical ranking with uncertainty, bias correction, difficulty stratification, and a
trait-grounded morphological axis _on top of_ held-out-scan objective metrics — and spans whole-
plant, procedural, reconstruction, and molecular modalities in one platform. A recurring, honest
finding motivating this design is that geometry-to-a-single-reference is insufficient:
morphologically incomplete outputs (an isolated organ, a partial plant) can score well on Chamfer
distance yet are wrong as _plants_.

## Leaderboard governance

Finally, **Singh et al.** (arXiv:2504.20879, "The Leaderboard Illusion") document how undisclosed
private testing, selective score retraction, and data-access asymmetry can distort arena rankings.
We treat this critique as a design requirement rather than an afterthought: entry policy,
absence of private pre-testing, principled (not selective) exclusions, per-generator vote-count and
confidence disclosure, and a no-silent-deprecation rule are published on a public coverage page,
and verified sign-in yields a higher-integrity vote pool. To our knowledge no other generative-3D
arena publishes a comparable governance surface.

## Positioning

| Capability                                                | **Taxon3D** | 3D Arena | 3DGen-Bench | GenAI-Arena | Plant Methods |
| --------------------------------------------------------- | ---------------- | -------- | ----------- | ----------- | ------------- |
| Biological/plant domain                                   | ✓                | ✗        | ✗           | ✗           | ✓             |
| Live human voting + leaderboard                           | ✓                | ✓        | ✓           | ✓           | ✗             |
| Elo + Bradley–Terry + bootstrap CIs                       | ✓                | ~        | ~           | ✓           | ✗             |
| Held-out-scan GT (Chamfer/F-score)                        | ✓                | ✗        | ~           | ✗           | ✓             |
| Calibrated VLM judge (chance-corrected κ)                 | ✓                | ✗        | ~           | ✗           | ✗             |
| Position/format bias audit (corrected)                    | ✓                | ~        | ✗           | ✗           | ✗             |
| Vote integrity (gold checks + trust gating)               | ✓                | ~        | ✗           | ✗           | —             |
| Published governance / submission policy                  | ✓                | ✗        | ✗           | ✗           | ✗             |
| Multi-modal span (plant + procedural + recon + molecular) | ✓                | ✗        | ✗           | ✗           | ✗             |

The gap Taxon3D does not yet close is _scale_: with vote volume far below 3D Arena's, our
Bradley–Terry intervals remain wide and many ranks are provisional. This is a matter of
participation over time rather than of method, and we report it transparently on the coverage page.

---

**Citation-audit TODO (before submission):** run `ghostcite` (byline + retraction gate) over every
anchor above; confirm the +144.1/+16.6 figures and the 123,243/8,096/19 counts against the 3D
Arena camera-ready; confirm the Plant Methods author order and the 3DGen-Bench first author.
(K-Sort Arena first author = Zhikai Li — verified 2026-07-01 via arXiv + Semantic Scholar.)
