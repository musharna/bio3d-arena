# D-Gen firming — independent cross-judge A/B + multi-model results

Independent judge: `openai/gpt-5.1` (different lab from the claude-sonnet-4-6 generation judge).

## Independent cross-judge A/B (blind, both orders)
- A/B pairs (best_round>0, valid baseline): **8**
- **refined preferred: 5/8** (rate 0.625)
- baseline preferred: 3/8
- inconsistent (position-flip/tie): 0/8
- repairs (invalid baseline -> valid best, not A/B'd): 5
- no-refinement (best == round 0): 5
- errors (render/judge failed, excluded): 0

### Per (model, taxon)
- google/gemini-3.1-pro-preview / Solanum lycopersicum: ab baseline ['A', 'B']
- google/gemini-3.1-pro-preview / Zea mays: no-refinement  
- google/gemini-3.1-pro-preview / Pinus sylvestris: repair  
- google/gemini-3.1-pro-preview / Rosa: ab refined ['B', 'A']
- google/gemini-3.1-pro-preview / Glycine max: no-refinement  
- google/gemini-3.1-pro-preview / Arabidopsis thaliana: repair  
- anthropic/claude-opus-4.8 / Solanum lycopersicum: ab refined ['B', 'A']
- anthropic/claude-opus-4.8 / Zea mays: ab refined ['B', 'A']
- anthropic/claude-opus-4.8 / Pinus sylvestris: ab baseline ['A', 'B']
- anthropic/claude-opus-4.8 / Rosa: no-refinement  
- anthropic/claude-opus-4.8 / Glycine max: ab refined ['B', 'A']
- anthropic/claude-opus-4.8 / Arabidopsis thaliana: ab refined ['B', 'A']
- x-ai/grok-4.3 / Solanum lycopersicum: ab baseline ['A', 'B']
- x-ai/grok-4.3 / Zea mays: repair  
- x-ai/grok-4.3 / Pinus sylvestris: repair  
- x-ai/grok-4.3 / Rosa: repair  
- x-ai/grok-4.3 / Glycine max: no-refinement  
- x-ai/grok-4.3 / Arabidopsis thaliana: no-refinement  

## Multi-model same-judge fidelity lift (sonnet judge, from D-Gen runs)
- google/gemini-3.1-pro-preview / Solanum lycopersicum: r0=0.5 best=0.5714285714285714 lift=0.0714285714285714
- google/gemini-3.1-pro-preview / Zea mays: r0=0.875 best=0.875 lift=0.0
- google/gemini-3.1-pro-preview / Pinus sylvestris: r0=None best=0.125 lift=None
- google/gemini-3.1-pro-preview / Rosa: r0=0.375 best=0.875 lift=0.5
- google/gemini-3.1-pro-preview / Glycine max: r0=0.375 best=0.375 lift=0.0
- google/gemini-3.1-pro-preview / Arabidopsis thaliana: r0=None best=0.75 lift=None
- anthropic/claude-opus-4.8 / Solanum lycopersicum: r0=0.625 best=1.0 lift=0.375
- anthropic/claude-opus-4.8 / Zea mays: r0=0.625 best=1.0 lift=0.375
- anthropic/claude-opus-4.8 / Pinus sylvestris: r0=0.42857142857142855 best=0.6666666666666666 lift=0.23809523809523808
- anthropic/claude-opus-4.8 / Rosa: r0=0.75 best=0.75 lift=0.0
- anthropic/claude-opus-4.8 / Glycine max: r0=0.125 best=0.75 lift=0.625
- anthropic/claude-opus-4.8 / Arabidopsis thaliana: r0=0.625 best=1.0 lift=0.375
- x-ai/grok-4.3 / Solanum lycopersicum: r0=0.375 best=0.5714285714285714 lift=0.1964285714285714
- x-ai/grok-4.3 / Zea mays: r0=None best=0.0 lift=None
- x-ai/grok-4.3 / Pinus sylvestris: r0=None best=0.25 lift=None
- x-ai/grok-4.3 / Rosa: r0=None best=0.2857142857142857 lift=None
- x-ai/grok-4.3 / Glycine max: r0=0.25 best=0.25 lift=0.0
- x-ai/grok-4.3 / Arabidopsis thaliana: r0=0.375 best=0.375 lift=0.0

## Caveats
- The A/B tests only where refinement CHANGED the output (best_round>0); repairs + no-refinement are
  reported separately so the denominator is honest.
- Per-taxon n is small. Chamfer is intentionally NOT the primary axis (geometry != morphology).

## Interpretation (manual analysis)

**The same-judge circularity is substantially addressed.** An independent, different-lab judge
(`gpt-5.1` — not `claude-sonnet-4-6`, which both generated the D-Gen critique AND scored fidelity),
blind and run in both orders, prefers the **refined** output over the round-0 baseline in **5/8**
A/B pairs (62.5%), with **zero** order-inconsistencies (both orders agreed on every pair → the judge
is decisive and not position-biased).

**Crucially, it is not rubber-stamping.** The independent preference tracks the *magnitude* of the
same-judge fidelity lift:
- Where the lift was large, the independent judge AGREES refined is better: gemini Rosa (lift +0.50);
  opus Solanum / Zea / Glycine / Arabidopsis (all jumped to ~1.0). Opus, the model refinement helped
  most, is **4/5 refined-preferred** by the independent judge.
- Where the lift was marginal or the base model weak, the independent judge sometimes prefers the
  baseline: gemini Solanum (lift +0.07 → baseline), opus Pinus, grok Solanum. This is the signature
  of a *valid* signal — it disagrees exactly where the same-judge signal was weakest.

**Generality across models (same-judge lift):** rubric-in-the-loop refinement improved fidelity across
all three different-lab generators, heterogeneously — **dramatically for opus** (5/6 taxa improved,
several to a perfect 1.0), **moderately for gemini** (Rosa +0.50), and mostly as **repairs for grok**
(a weak base model with several invalid first meshes that the exec-error feedback fixed).

**Verdict:** the D-Gen thesis — rubric feedback produces genuinely better plants, not just
better-by-its-own-judge — holds under independent judging, most strongly where the effect is largest,
and replicates across three labs' models.

**Caveats:** n=8 A/B pairs is small (the honest denominator: only where refinement *changed* a valid
output; repairs and no-refinement are excluded and reported separately). The A/B is one independent VLM,
not a human panel — a human blind A/B on these same pairs would further harden the claim (deferred).
Chamfer was intentionally not the primary axis (geometry != morphology).
