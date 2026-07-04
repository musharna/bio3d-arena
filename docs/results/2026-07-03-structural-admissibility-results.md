# Structural admissibility — real-execution acceptance gate

**Run:** `scripts/score_structural.py` over a COPY of the study DB (`audit-arena.db`) with the real
GLB assets (bio3d-arena-mvp worktree), 2026-07-03. Structural predicate `structural-v1`, conservative
thresholds `MIN_VERTS=8`, `MIN_FACES=8`, `MIN_EXTENT_RATIO=0.02`.

## Result: merge-blocker PASSED

- **294 outputs evaluated, 0 errors, 47 rejected.**
- **Zero false positives on good outputs.** Of the 47 rejects, **0** have completeness category
  `complete`. `MIN_EXTENT_RATIO=0.02` rejected no good mesh — including slender **Pinus** outputs
  (all 10 flagged pines were _admitted_), which was the pre-run false-positive worry. The
  precision-first acceptance criterion (zero good-output rejects) is satisfied; the threshold did
  not need loosening.

### Rejects by reason

| reason            | n   | what it is                                                                                                                              |
| ----------------- | --- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `empty`           | 43  | 0 verts / 0 faces meshes (e.g. `plant3d`-source outputs) — genuinely empty/degenerate, mostly UNSCORED (bypassed the completeness gate) |
| `degenerate_bbox` | 4   | flat/sliver meshes, extent ratio 0.005–0.020 (2 collapsed maize recons, 1 pine, 1 soybean)                                              |

### Rejects by completeness category

`UNSCORED=43, fragment=2, isolated-organ=1, partial-organism=1, complete=0`. The structural predicate's
main coverage win is the **43 UNSCORED** degenerate outputs that the VLM completeness gate never saw.

## The honest finding: structural covers geometry, not semantics

Cross-tab against the 32 human audit flags: **structural caught only 1 of 32** (a genuinely-flat
maize, `degenerate_bbox`). The other 31 flags are _admitted_ by structural because they are
**semantically** invalid with **structurally valid geometry**:

- fruit-only tomato → valid mesh, wrong content (semantic-identity)
- multiple-plants / mature pine trees (retrieval) → valid mesh, wrong specimen (cardinality/identity)
- broken-but-has-organs (partcrafter) → valid-enough mesh
- partial models → valid geometry, incomplete

This **confirms the design thesis empirically**: geometric admissibility is a clean, cheap,
zero-false-positive first filter that removes true garbage (and covers unscored outputs), but the
failure classes a human actually flags need the **deferred semantic predicates (cardinality +
identity)**. The 32 flags are the labeled ground-truth to build and validate those against.

## Interpretation

Structural v1 ships as the correct foundation: the vote pool is now "admit iff all predicates pass",
with a domain-agnostic structural predicate live and completeness folded in as a peer predicate. It
removes 47 degenerate outputs at zero false-positive cost. It is _not_, on its own, a large reduction
in the human's audit burden — that is the next increment (semantic predicates), for which this
establishes the pluggable machinery.

## Reproduce

```
cp data/study/arena-study.db <copy>            # never serve/scored the real study DB
BIO3D_DATA_DIR=<assets-root> BIO3D_DATABASE_URL="sqlite:///<copy>" \
  PYTHONPATH="$(pwd)" .venv/bin/python scripts/score_structural.py
# then cross-tab admissibility(predicate='structural', admit=0) vs completeness.category + output_flag
```
