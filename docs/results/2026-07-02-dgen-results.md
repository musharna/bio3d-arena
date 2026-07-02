# D-Gen — rubric-in-the-loop refinement results (v1)

**Run:** `google/gemini-3.1-pro-preview`, 6 taxa × up to 3 rounds, against a copy of the study DB
(`scripts/run_dgen.py`, run_id=1, 2026-07-02). Reward = trait-morphology rubric fidelity
(`present_correct / assessable`) + completeness gate; loop refines by feeding the previous script +
an actionable critique (failing traits + missing organs + any Blender exec error) back to the model,
plateau-stopping and keeping the best round.

## Per-taxon trajectory (rubric fidelity by round; `*` = promoted best)

| Taxon                | r0           | r1          | r2           | best  | lift (r0→best)        |
| -------------------- | ------------ | ----------- | ------------ | ----- | --------------------- |
| Rosa                 | 0.375        | 0.750       | **0.875\***  | 0.875 | **+0.500**            |
| Solanum lycopersicum | 0.500        | **0.571\*** | timeout      | 0.571 | +0.071                |
| Zea mays             | **0.875\***  | 0.625       | —            | 0.875 | 0.000                 |
| Glycine max          | **0.375\***  | 0.286       | —            | 0.375 | 0.000                 |
| Arabidopsis thaliana | invalid_mesh | 0.625       | **0.750\***  | 0.750 | — (no valid baseline) |
| Pinus sylvestris     | invalid_mesh | **0.125\*** | invalid_mesh | 0.125 | — (no valid baseline) |

16 iterations total.

## Findings

- **Refinement helped or repaired 4 of 6 taxa.**
  - **Rosa** is the headline: three rounds of steady, monotonic gains, **0.375 → 0.875 (+0.50)** — the
    critique ("fix these traits") drove real improvement round over round.
  - **Solanum** improved modestly (+0.07) before a round-3 render timeout.
  - **Arabidopsis and Pinus** had an **invalid first mesh** (gemini's round-0 Blender script produced
    no valid geometry); the exec-error feedback **repaired** the script into a valid, scored plant —
    Arabidopsis reached **0.75**. This is the loop lifting _validity_ (pass@1), not just fidelity, and
    is the most objective win (it doesn't depend on the rubric judge's scale).
- **On 2 of 6 taxa (Zea mays, Glycine max) the model's revision REGRESSED** (round 1 scored lower than
  round 0). Notably Zea mays started strong (0.875) and the "fix these traits" critique led the model
  to break something already good — a known over-correction failure mode of naive self-refinement.
  **No harm was done:** plateau-stop + best-selection kept round 0, so the promoted output is the best
  seen, never a regression.
- **Aggregate over the 4 taxa with a valid round-0 baseline:** mean lift **+0.143**; **2/4 improved**,
  2/4 flat-but-protected. Counting the 2 repaired-from-invalid taxa, **4/6 taxa ended better than their
  first attempt**.

## Interpretation + validity

Rubric-in-the-loop refinement produces **real, heterogeneous** gains: dramatic where the first draft is
mediocre-but-valid (Rosa), repair where the first draft is broken (Arabidopsis, Pinus), and
correctly-suppressed regression where the first draft is already good (Zea mays). The plateau-stop +
best-selection is load-bearing — it converts a noisy per-round signal into a monotonic "never worse than
the baseline" guarantee.

**Caveat (as designed):** fidelity is scored by the _same_ VLM rubric judge that generates the critique,
so the headline claim is scoped **"rubric feedback raises rubric-judged fidelity."** Per-taxon n is small
(~7–8 traits). The invalid→valid repairs are the judge-independent part of the result. A stronger claim
would need an independent check (held-out Chamfer where scans exist, or a blind human A/B) — deferred.

## Reproduce

```
set -a; . /path/to/.env; set +a           # OPENROUTER_API_KEY (+ ANTHROPIC_API_KEY in env)
BIO3D_DATABASE_URL="sqlite:///<copy-of-study.db>" PYTHONPATH="$(pwd)" \
  .venv/bin/python -u scripts/run_dgen.py --model google/gemini-3.1-pro-preview --max-rounds 3
# resume after an interruption: add --run-id <id> (skips taxa already committed)
```

The best refined output per taxon is promoted (in the copy DB) as a votable `source="commissioned"`
output under generator `openrouter-google-gemini-3-1-pro-preview-dgen` (paradigm `procedural_llm`), so it
appears on `/procedural` next to the one-shot generator. Promoting into the real arena DB is a separate
step (run against the study DB, or import), out of scope for this experiment.
