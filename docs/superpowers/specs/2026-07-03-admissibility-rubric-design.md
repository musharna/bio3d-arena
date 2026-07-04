# Admissibility rubric — pluggable pre-vote gate + structural predicate (design)

**Status:** approved design, 2026-07-03. Feeds `writing-plans`.

## Problem

An arena conflates two judgments that should be separate:

1. **Admissibility** (binary, per output): is this a well-formed instance of what the task asked
   for — a _valid candidate for voting_ — at all?
2. **Preference** (relative, per pair): given two admissible candidates, which is better?

Bradley-Terry/Elo assume every comparison is a meaningful preference; comparing a valid plant
against a degenerate mesh is an admissibility test wearing a preference's clothes, and it pollutes
the ranking. Today the pool gate (`_vote_excluded`) is an ad-hoc list of plant-specific filters
(reference-scan, untextured, D-Complete completeness category, `hidden_at`). This spec introduces a
**general admissibility abstraction**: a pool that admits an output iff every predicate in the
task's rubric passes, with predicates that are pluggable and (for structural/geometric ones)
domain-agnostic.

### Grounding (live audit, 2026-07-03)

A user flagged 32 outputs across four failure classes; joined to what the auto-gate saw:
`isolated-organ/fragment=0` (the completeness gate already removes these — none reached the user),
`UNSCORED=12` (the ~87 outputs with no completeness score bypass the gate entirely — mostly
retrieval + some recon + authored procedural), `complete-but-invalid=17` (the metric measures
organ _presence_, not _validity_), `partial-organism=3` (kept by design). The two biggest leaks —
unscored coverage and geometrically-broken outputs — are exactly what a cheap, domain-agnostic
**structural** predicate closes.

## Scope (locked)

**In:** the admissibility abstraction (predicate protocol + registry + named rubric + one composer
the pool gate calls); the **structural-validity predicate** as the first concrete predicate
(trimesh, precomputed + stored, conservative/precision-first); folding the existing completeness
gate in as a second predicate behind the same composer.

**Deferred (follow-on specs, non-goals here):** cardinality & semantic-identity predicates (the
multiple-plants / complete-but-invalid class); per-task rubric _configuration_ (this spec ships one
global default rubric); the flag→re-fit active-learning loop; converting reference-scan/untextured
into predicates.

## Design principles (approved)

- **Precision-first / conservative.** A false positive (auto-rejecting a legitimate output) acts
  _silently_ and biases the ranking; a false negative is caught by the human flag (reviewable). So
  the structural predicate rejects only _unambiguous_ degeneracy and must have **zero false
  positives on valid thin organic meshes**. The auto-gate is a scalpel; the flag is the net.
- **Generalize by swapping rubric params, machinery unchanged.** Structural/geometric predicates
  are fully domain-agnostic (a chair, a molecule, a plant — "is the mesh degenerate" is identical).
  Semantic predicates swap a rubric but reuse the machinery. The rubric is a named list of predicate
  names; a future per-task/per-domain rubric swaps the list, not the code.

## Architecture (Approach A: predicate protocol + composed gate + precomputed results)

### `app/admissibility.py` — the abstraction (new)

```python
@dataclass(frozen=True)
class Verdict:
    admit: bool
    reason: str          # "" when admit; else a short machine code e.g. "degenerate_bbox"
    detail: dict         # predicate-specific evidence (counts, extents…) for auditability

class Predicate(Protocol):
    name: str
    version: str
    def rejected_output_ids(self, db) -> set[int]: ...   # ids this predicate does NOT admit

# Registry + default rubric (a named list of predicate names — the generalization seam).
REGISTRY: dict[str, Predicate]          # name -> instance
DEFAULT_RUBRIC: list[str] = ["structural", "completeness"]

def non_admitted_output_ids(db, rubric: list[str] | None = None) -> set[int]:
    """Union of rejected ids across the rubric's active predicates. The single function the
    pool gate calls. rubric=None -> DEFAULT_RUBRIC."""
```

`rejected_output_ids(db) -> set[int]` (set-returning, precomputed-source) keeps the pool gate O(1)
per output, exactly like today's `_gated` set. Each predicate reads its own precomputed source.

### `app/structural.py` — the structural predicate (new)

Pure geometry via **trimesh** (already a dependency; repo idiom `trimesh.load(path, force="mesh")`
with `trimesh.Scene` handling). Resolves the file as `os.path.join(config.ASSET_DIR, output.asset_path)`.

`evaluate_glb(path) -> Verdict` rejects (admit=False) **only** for unambiguous degeneracy:

- **empty** — load fails, `trimesh.Scene` with no geometry, or 0 vertices/0 faces.
- **non_finite** — any NaN/inf in vertices.
- **too_small** — vertex count < `MIN_VERTS` or face count < `MIN_FACES` (tiny floors, e.g. 8/8 —
  a single triangle is 3/1; a real plant mesh has thousands).
- **degenerate_bbox** — the smallest bounding-box extent divided by the bbox diagonal is below
  `MIN_EXTENT_RATIO` (a near-flat/sliver object like the grey triangle; a real 3D plant is not flat).

Everything else admits. Thresholds (`MIN_VERTS`, `MIN_FACES`, `MIN_EXTENT_RATIO`) are module
constants, **tuned empirically** so the predicate rejects the flagged geometrically-degenerate set
and admits 100% of the good/"complete" outputs (see Validation). NOT rejected here (deliberately —
subtler, and not reliably structural for organic meshes): high connected-component count
("multiple plants" — plants legitimately have many detached leaf components), non-watertight (most
valid plant meshes are open), texture presence.

Persistence mirrors D-Complete's `enumerate_work → evaluate → upsert`:

- New `Admissibility` model (below); `upsert_verdict(db, output_id, predicate, verdict, version)`
  (unique `(output_id, predicate)`, rescore overwrites).
- `enumerate_structural_work(db) -> list[output_id]` (outputs lacking a current-version structural
  verdict). `evaluate_outputs(db, ids)` loads each GLB, computes, upserts; fail-loud-per-output
  (one unreadable GLB → record a verdict/skip, never abort the batch).
- `scripts/score_structural.py` — the backfill driver (no VLM, no browser; fast — trimesh only).
- Ingest hook: `app/ingest.py` (and `commission.ingest_attempt`) compute the structural verdict for
  a newly-created output so new assets are gated from first appearance. (Guarded so an unreadable
  asset logs + records a reject, never breaks ingest.)

### `Admissibility` model (new, `app/models.py`)

`id, output_id (FK model_output.id, index), predicate (str), admit (bool), reason (str),
detail_json (str), version (str), computed (dt)`; `UniqueConstraint(output_id, predicate)`.
It is a **ModelOutput child** → it MUST be added to `app/seed.py` `_FORCE_DELETE_MODELS`; the
`tests/test_seed_force_cascade.py` drift-guard will FAIL until it is (intended backstop). Created by
`create_all`; brand-new table so no `_ensure_columns` self-heal needed.

### The `completeness` predicate (fold-in, no re-scoring)

A `CompletenessPredicate.rejected_output_ids(db)` returns ids whose latest `Completeness.category ∈
config.POOL_EXCLUDED_COMPLETENESS_CATEGORIES` — i.e. exactly today's
`flags.excluded_output_ids_by_completeness`, now expressed as a predicate. Reuse that function as
the implementation (no behavior change; DRY).

### Pool gate wiring (`app/main.py` `_build_comparison`)

Replace the completeness-only precompute (currently `flags.excluded_output_ids_by_completeness`,
main.py:276) with the composer:

```python
_gated = admissibility.non_admitted_output_ids(db)   # structural ∪ completeness
def _vote_excluded(o):
    return (is_reference_scan(o.source) or is_untextured_output(o)
            or o.hidden_at is not None or o.id in _gated)
```

Same set precomputed once; same pick_task/pick_pair parity (the invariant that prevents the
intermittent-404 class). `hidden_at` and reference/untextured stay inline for now (folding them into
predicates is a deferred non-goal).

## Data flow

```
ingest new output ─► structural.evaluate ─► Admissibility(upsert)      (new assets gated on arrival)
backfill (existing) ─ scripts/score_structural.py ─► Admissibility     (covers the ~87 unscored + all)
/api/next ─► _build_comparison ─► admissibility.non_admitted_output_ids(db)
             = structural.rejected ∪ completeness.rejected   ─► _vote_excluded (∪ ref/untextured/hidden)
```

## Validation (the real acceptance gate)

Run `scripts/score_structural.py` over a COPY of the study DB + real GLBs, then report against the
32 audit flags and a sample of good outputs:

- **Zero false positives:** 100% of "complete"/known-good outputs are admitted by structural.
  This is a hard gate — a single legitimate output rejected fails the criterion (tighten thresholds).
- **Recall on the degenerate subset:** how many of the flagged broken/unscored outputs structural
  rejects. Reported honestly — structural only catches _geometric_ degeneracy; broken-but-
  geometrically-fine outputs are the later identity predicate's job, and that's stated, not hidden.
- Written to `docs/results/2026-07-03-structural-admissibility-results.md`.

NEVER run against `data/study/arena-study.db`; use a copy.

## Testing

- **Unit (structural):** synthetic trimesh cases — empty scene, single triangle (3 verts/1 face →
  too_small AND degenerate_bbox), NaN vertex → non_finite, a tiny box, and a valid multi-component
  "plant-like" mesh (thousands of verts, several components) → admits. Each maps to the right reason.
- **admissibility composer:** `non_admitted_output_ids` = union of a fake structural predicate + the
  real completeness predicate; empty rubric → empty set; unknown predicate name → fail-loud.
- **Pool:** an output with a reject structural verdict is never returned by `_build_comparison`
  (DB-truth via `Comparison`), and pick_task parity holds (no 404 regression) — reuse the
  `test_pool_autogate` pattern.
- **Persistence:** `upsert_verdict` overwrites on re-run (unique constraint); `enumerate_structural_work`
  skips current-version verdicts; fail-loud-per-output on an unreadable GLB.
- **Seed cascade:** `Admissibility` is in `_FORCE_DELETE_MODELS` (the existing drift-guard test
  enforces it).
- **Real-execution:** the Validation run above is the real-execution check (trimesh on real GLBs).

## Global constraints

- Test runner `.venv/bin/pytest`, `BIO3D_DATABASE_URL` UNSET; NEVER `=study`. Current baseline
  660 passed / 8 skipped.
- Reuse: `trimesh` (repo idiom `load(..., force="mesh")` + Scene handling), the D-Complete
  `enumerate/evaluate/upsert` + backfill-script pattern, `flags.excluded_output_ids_by_completeness`
  (as the completeness predicate's body), the `exclude_fn` pick_task/pick_pair parity, `_utcnow`.
- New `Admissibility` table via `create_all` + added to `_FORCE_DELETE_MODELS`.
- Structural predicate is precomputed/stored (never loads GLBs per `/api/next` request).
- Precision-first: zero false positives on good outputs is a merge-blocking acceptance criterion.

## Non-goals (YAGNI)

- No cardinality or semantic-identity predicate (multiple-plants / complete-but-invalid).
- No per-task rubric configuration UI/API (one global default rubric).
- No flag→re-fit loop; no admin surface for admissibility verdicts.
- No change to reference-scan/untextured/hidden handling (stays inline).
- No new dependency (trimesh already present).
