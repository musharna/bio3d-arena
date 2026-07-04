# Semantic-admissibility predicate — VLM cardinality+identity gate (design)

**Status:** approved design, 2026-07-03. Feeds `writing-plans`. Branch `p2-completeness`.

## Problem

The structural-admissibility predicate (shipped, `structural-v1`) is a clean, zero-false-positive
geometric filter, but the acceptance run proved its ceiling: it caught **1 of 32** human audit
flags. The other 31 are **semantically** invalid outputs with **structurally valid geometry** —
fruit-only tomatoes, multiple-plants/mature-tree retrievals, broken-but-has-organs part-based
models, and partials. Geometry cannot see any of these; a human looking at a render can, in one
glance. This predicate closes that gap with a VLM judge, under the same conservative admissibility
contract structural established.

### Grounding (the labeled ground truth)

The user's live audit flagged 32 outputs across four classes (see
`docs/results/2026-07-03-structural-admissibility-results.md`):

- **fruit-only** (mainly tomato) — a valid mesh of the wrong _content_ (a sub-part, not the organism).
- **broken** (part-based modeling) — geometrically fine-enough, semantically incoherent.
- **multiple-plants** (some trees, a maize specimen) — a valid mesh of the wrong _cardinality_.
- **partial** (lots of pine, some procedural arabidopsis) — valid geometry, incomplete organism.

These 32 flags are the **validation ground truth** for this predicate.

## The admissibility-vs-preference frame (unchanged)

Admissibility is the binary per-output gate ("is this a valid candidate for voting at all?") that
runs _before_ the pairwise preference pool. This spec adds a second concrete predicate behind the
existing composer (`app/admissibility.non_admitted_output_ids`); the machinery is unchanged. The
generalization seam is intact: a predicate is a named entry in the rubric list; the semantic
predicate is domain-aware (it knows "plant") but the _shape_ — one VLM call → structured verdict →
persisted `Admissibility` row → `rejected_output_ids` — is reusable for any domain by swapping the
tool prompt.

## Scope (locked)

**In:** one combined **semantic-admissibility predicate** — a single VLM tool-use call over an
output's contact sheet returning a discrete verdict (`ok / multiple / sub_part / not_a_plant /
wrong_species / uncertain`); persistence as `Admissibility` rows under `predicate="semantic"`
(reusing `structural.upsert_verdict`, **no schema change**); a `SemanticPredicate` for the composer;
a backfill script; a **mode switch** (`off / advisory / gate`) so the acceptance run decides whether
it auto-excludes or only surfaces to the human ⚑ queue; the acceptance/validation run against the 32
flags.

**Deferred / non-goals (YAGNI):**

- **No ingest hook.** A VLM call + a rendered contact sheet is far too heavy for the synchronous
  ingest path. Semantic verdicts are computed by the batch script only (mirrors completeness, which
  also has no ingest integration). New outputs get a semantic verdict on the next batch run.
- **No cardinality/identity split.** One predicate; the `reason` field preserves the sub-class. The
  32 flags do not cleanly separate cardinality from identity, so two predicates would overbuild a
  distinction the ground truth does not support.
- **No per-task / per-domain rubric configuration** (one global default rubric, as today).
- **No new dependency** (Anthropic client + contact-sheet render already present via completeness).
- **No change** to reference-scan / untextured / `hidden_at` handling (stays inline in the pool gate).

## Design principles (inherited from structural, approved)

- **Precision-first / conservative gate.** A false positive (auto-rejecting a legitimate output)
  acts _silently_ and biases the ranking; a false negative is caught by the human ⚑ flag
  (reviewable). The judge is prompted to reject **only** when clearly inadmissible; **`uncertain`
  maps to admit**. Zero false positives on good outputs is a **merge-blocking** acceptance criterion,
  exactly as for structural.
- **Empirical gate decides gating-vs-advisory, not a guess.** VLM reliability is uncertain
  (D-Complete shipped "experimental / non-gating" when it could not clear κ≥0.6). So the predicate
  ships **advisory by default** and is promoted to **gating** only if the acceptance run shows zero
  false positives on good outputs. The mode is a config flag; flipping it is a one-line change, no
  code change.

## Architecture

### `app/semantic.py` (new) — the predicate

Clones the completeness VLM-judge pattern (`app/completeness.py`) for scoring and reuses the
structural persistence helpers (`app/structural.py`) for storage.

```python
VERSION = "semantic-v1"

# Discrete verdict codes. admit iff verdict in ADMIT_CODES.
ADMIT_CODES = {"ok", "uncertain"}
REJECT_CODES = {"multiple", "sub_part", "not_a_plant", "wrong_species"}

# Advisory flags use one synthetic session id so each output gets at most one semantic
# flag (record_flag is idempotent per (output, session_id)), and a sentinel threshold so
# an advisory flag NEVER auto-hides the output — advisory surfaces, it does not remove
# from the pool (that is gating). record_flag requires a non-null session_id.
SEMANTIC_FLAG_SESSION = "semantic-v1"
ADVISORY_NO_HIDE_THRESHOLD = 10**9

SEMANTIC_TOOL = {
    "name": "record_admissibility",
    "description": "Judge whether the rendered model is a single, whole, valid plant specimen.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["ok", "multiple", "sub_part", "not_a_plant", "wrong_species", "uncertain"],
            },
            "note": {"type": "string"},
        },
        "required": ["verdict", "note"],
    },
}

def verdict_from_code(code: str) -> Verdict:
    """Map a VLM verdict code to an admissibility Verdict. admit iff code in ADMIT_CODES;
    an unrecognized code is treated as 'uncertain' (admit) — precision-first."""

def score_semantic(client, sheet_png: bytes, *, taxon: str | None) -> dict:
    """One VLM call over the contact sheet; returns {'verdict': str, 'note': str}."""

def enumerate_semantic_work(db) -> list[dict]:
    """One row {'output_id', 'taxon'} per eligible output lacking a current-VERSION semantic
    verdict. Eligible = non-gold, non-reference-scan, non-untextured (structural's breadth, NOT
    completeness's taxon-inventory gate). taxon = TraitRubric.taxon for the output's task if a
    rubric exists, else None."""

def evaluate_outputs(db, work, *, client, sheet_for, emit_flags: bool) -> dict:
    """Score each work row: sheet_for(oid) -> contact sheet, score_semantic, map to Verdict,
    upsert (structural.upsert_verdict, predicate='semantic', VERSION) — persistence is
    UNCONDITIONAL (the acceptance run reads these rows regardless of mode). Fail-loud per
    output. If emit_flags AND the verdict rejects, also record a non-hiding advisory
    OutputFlag: flags.record_flag(db, oid, SEMANTIC_FLAG_SESSION, reason=verdict.reason,
    threshold=ADVISORY_NO_HIDE_THRESHOLD). Caller commits. The backfill script sets
    emit_flags = (config.SEMANTIC_ADMISSIBILITY_MODE == 'advisory')."""

class SemanticPredicate:
    name = "semantic"
    version = VERSION
    def rejected_output_ids(self, db) -> set[int]:
        # Admissibility rows where predicate='semantic' AND admit is False.
```

**The prompt (precision-first, taxon-opportunistic).** `_build_messages(png, taxon)` asks: _"This
is a contact sheet of a generated 3D model, rendered from several angles. Judge whether it is a
single, whole, valid plant specimen. Reject as: `multiple` (more than one distinct plant / a scene),
`sub_part` (only a detached organ — a single fruit, leaf, or flower — not a whole plant),
`not_a_plant` (not a recognizable plant at all — a blob, a non-plant object), `wrong_species` (a
plant, but clearly not a {taxon}). Otherwise `ok`. If you cannot tell, answer `uncertain`. Only
reject when clearly inadmissible; when in doubt, prefer `ok` or `uncertain`. Then call
record_admissibility."_ When `taxon is None`, the `wrong_species` clause is omitted (the four other
codes are taxon-agnostic).

### Persistence — reuse, no new table

- **Storage:** `structural.upsert_verdict(db, output_id, predicate="semantic", verdict, VERSION)` —
  already generic over predicate name; one `(output_id, predicate)` row, overwrite on rescore.
- **Contact sheet:** injected via a `sheet_for(oid) -> png bytes` callback, exactly as
  `completeness.score_outputs` does. Reuse the existing render path: `app/judge_render.py`
  (`contact_sheet_path(output_id, condition)` for the deterministic cache location,
  `render_contact_sheets(db, output_ids, condition, *, capture_multi)`, `tile_contact_sheet`) with
  `scripts/judge_capture.browser_capture_multi_factory()` supplying `capture_multi`. A sheet already
  cached from a prior completeness/judge run is reused directly (no browser); an UNSCORED output with
  no cached sheet is rendered. The **same contact-sheet condition** completeness used is reused so
  cached sheets hit.
- **`scripts/score_semantic.py`** — the backfill driver. Two existing completeness backfills are the
  templates: **`scripts/score_completeness_from_sheets.py`** (reads cached sheets — cheap, no
  browser) and **`scripts/score_completeness.py`** (renders via `capture_multi`). The semantic driver
  follows the same shape: open a DB (a **copy** for the acceptance run), build the Anthropic client,
  `enumerate_semantic_work`, resolve each `sheet_for(oid)` (cached-else-render),
  `evaluate_outputs(..., emit_flags=<mode=='advisory'>)`, commit, print a summary.

### Composer + mode wiring (`app/admissibility.py`, `app/config.py`, `app/main.py`)

- **Registry:** add `SemanticPredicate()` to `_registry()` (function-local import, same pattern as
  `StructuralPredicate`).
- **Config:** `SEMANTIC_ADMISSIBILITY_MODE: str = env("SEMANTIC_ADMISSIBILITY_MODE", "advisory")`
  in `app/config.py`, one of `off | advisory | gate`.
- **Effective rubric:** `non_admitted_output_ids` composes `DEFAULT_RUBRIC` plus `"semantic"` **iff**
  `config.SEMANTIC_ADMISSIBILITY_MODE == "gate"`. `DEFAULT_RUBRIC` stays `["structural",
"completeness"]`; semantic is appended dynamically based on the mode so `advisory`/`off` never
  auto-exclude. (Implementation: a small `_effective_rubric()` helper reading the config, or the
  composer appends `"semantic"` when the mode is `gate`.)
- **Pool gate (`main.py` `_build_comparison`):** unchanged call site —
  `admissibility.non_admitted_output_ids(db)` already picks up semantic when the mode is `gate`.
  Same set precomputed once; same pick_task/pick_pair parity (the invariant that prevents the
  intermittent-404 class).

### Advisory surfacing

The `mode` controls two independent things; verdict **persistence is always on** when the batch
runs (so the acceptance run can cross-tab), regardless of mode:

- `gate` → `"semantic"` is in the effective rubric → `non_admitted_output_ids` auto-excludes rejects
  from the pool. No advisory flags.
- `advisory` → **not** in the rubric (no auto-exclude); the batch records a **non-hiding**
  `OutputFlag` per confident-reject: `flags.record_flag(db, oid, SEMANTIC_FLAG_SESSION,
reason=verdict.reason, threshold=ADVISORY_NO_HIDE_THRESHOLD)`. The sentinel threshold guarantees
  the advisory flag never trips `hidden_at` on its own — it only surfaces the reject into the
  existing ⚑ moderation/review queue the user already uses. (If a _human_ independently flags the
  same output, the human vote path applies the human threshold as usual; the semantic flag simply
  counts as one corroborating session.)
- `off` → not in the rubric and no flags emitted; verdicts persist if the batch is run, but are
  otherwise inert.

**Known limitation (v1, acceptable):** advisory flags are not retracted on rescore — if an output's
verdict flips reject→admit on a later batch, the stale advisory `OutputFlag` persists (a human
clears it via moderation). Low priority: advisory is the pre-gate state; once promoted to `gate`,
the rubric reflects the current verdict directly.

This is the safety net the user chose (option a): the predicate reduces audit burden the moment it
can gate, but until the acceptance run proves zero-FP on good outputs, its output is human-reviewed,
not silently applied.

## Data flow

```
backfill (copy) ─ scripts/score_semantic.py ─► enumerate_semantic_work
   ─► sheet_for(oid) ─► score_semantic(client, sheet, taxon) ─► verdict_from_code
   ─► upsert_verdict(predicate='semantic')            (+ record_flag if mode=advisory & reject)

/api/next ─► _build_comparison ─► admissibility.non_admitted_output_ids(db)
             = structural ∪ completeness ∪ (semantic iff mode=='gate')   ─► _vote_excluded
```

## Validation (the real acceptance gate)

Run `scripts/score_semantic.py` over a **COPY** of the study DB with the real GLB assets, then
cross-tab the `predicate='semantic'` verdicts against the 32 audit flags and the `complete`/known-good
outputs. Report in `docs/results/2026-07-03-semantic-admissibility-results.md`:

- **Zero false positives (merge-blocker):** 0 outputs with completeness category `complete` (and 0
  of a sampled good set) are rejected by semantic. A single legitimate reject fails the criterion →
  the predicate ships `advisory`, not `gate`.
- **Recall on the 31 semantic flags:** how many of the fruit-only / multiple / broken / partial
  flags semantic rejects, broken out by verdict code. Reported honestly — this is the number that
  justifies the predicate over structural-alone.
- **Decision:** if zero-FP passes, flip `SEMANTIC_ADMISSIBILITY_MODE` default to `gate`; else keep
  `advisory` and record why (which good outputs it wrongly rejected).

NEVER run against `data/study/arena-study.db`; use a copy. NEVER run pytest or serve with
`BIO3D_DATABASE_URL=study`.

## Testing

- **Verdict mapping (unit, no VLM):** `verdict_from_code` — each of the six codes → correct
  `admit`/`reason`; `uncertain` → admit; an unrecognized code → admit (precision-first).
- **Scoring (unit, stubbed client):** a fake Anthropic client returning a canned `record_admissibility`
  tool_use → `score_semantic` parses `{verdict, note}`; a response with no tool_use block fails loud.
- **Prompt taxon-gating:** `_build_messages(png, taxon="tomato")` includes the `wrong_species`
  clause and the taxon; `_build_messages(png, taxon=None)` omits both.
- **Enumerate breadth:** `enumerate_semantic_work` returns a taxon-less eligible output (task with no
  TraitRubric) with `taxon=None` — proving it reaches the UNSCORED set; excludes gold / reference-scan
  / untextured; skips outputs that already have a current-VERSION semantic verdict.
- **Persistence:** `evaluate_outputs` with a stubbed client + `sheet_for` upserts one semantic row
  per output (overwrites on re-run via the unique constraint); fail-loud per output on a raising
  `sheet_for` (recorded, batch continues).
- **Advisory surfacing:** `evaluate_outputs(..., emit_flags=True)` on a reject verdict upserts the
  `Admissibility` row **and** creates one non-hiding `OutputFlag` (session `SEMANTIC_FLAG_SESSION`,
  reason = the verdict code, and `hidden_at` stays None despite the flag); `emit_flags=False` upserts
  the row but creates no flag. An `ok`/`uncertain` verdict never creates a flag regardless of
  `emit_flags`.
- **Composer mode:** with a fake reject semantic verdict in the DB, `non_admitted_output_ids(db)`
  includes the id when `SEMANTIC_ADMISSIBILITY_MODE='gate'` and excludes it when `'advisory'` and
  `'off'` (config set per case).
- **Pool parity:** an output with a reject semantic verdict under `SEMANTIC_ADMISSIBILITY_MODE='gate'`
  is never returned by `_build_comparison`, and pick_task/pick_pair parity holds (no 404 regression)
  — reuse the `test_pool_autogate` pattern.
- **Real-execution:** the Validation run above (real VLM calls on real GLB renders) is the
  real-execution check.

## Global constraints

- Test runner `.venv/bin/pytest`, `BIO3D_DATABASE_URL` **UNSET**; NEVER `=study`. Baseline before
  this work: 679 passed / 8 skipped (post-admissibility).
- Reuse: `app/completeness.py` VLM-judge shape (`_img_block`, `_build_messages`, `_parse`,
  `score_*`, injected `sheet_for`, `JUDGE_MODEL`); `app/structural.py`
  `upsert_verdict`/`enumerate_*`/`evaluate_outputs`/`*Predicate` shape; `app/flags.record_flag` for
  advisory surfacing; `app/sourcing.is_reference_scan`/`is_untextured_output` for eligibility;
  `app/admissibility._registry`/`non_admitted_output_ids` composer; `app/judge_render.py`
  (`contact_sheet_path`/`render_contact_sheets`/`tile_contact_sheet`) +
  `scripts/judge_capture.browser_capture_multi_factory` for sheets; `scripts/score_completeness*.py`
  as the backfill-script templates.
- New `Admissibility` rows only — **no schema migration** (the table exists; `predicate` is a
  free-text column). `SemanticPredicate` reads precomputed rows (never calls the VLM at
  `/api/next` request time).
- Precision-first: zero false positives on good outputs is a merge-blocking acceptance criterion.
- Default `SEMANTIC_ADMISSIBILITY_MODE=advisory`; promote to `gate` only after the acceptance run.

## Non-goals (YAGNI) — recap

- No ingest hook (batch-only).
- No cardinality/identity predicate split (one predicate, `reason` carries the sub-class).
- No per-task rubric configuration UI/API.
- No new dependency.
- No change to reference-scan / untextured / hidden handling.
