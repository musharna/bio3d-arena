# Mode-C Rubric-Source Backends — Design

> Sub-project of Mode-C (botanical-trait GT). Unblocks Task 7 (the live trait-judge pass)
> by implementing the two trait-source backends that `scripts/build_trait_rubrics.py` ships
> as `NotImplementedError` stubs. Parent: `docs/superpowers/specs/2026-06-29-mode-c-trait-gt-design.md`.

## Problem

`build_trait_rubrics.py --live` cannot run: `fetch_db_traits` and `draft_llm_traits`
(build_trait_rubrics.py:61-76) are `NotImplementedError` stubs — T4 deferred the real
integrations as an implementation-time decision. With no rubrics authored, there is nothing
for `trait_judge.py` to score, so Mode-C's live pass is blocked. This sub-project builds the
two backends so rubrics can be authored, then T7 proceeds.

## Decisions (locked via brainstorming)

1. **Scope:** build a _scalable_ authoring pipeline (works for any taxon by name), but
   validate and ship only the 6 recon taxa first.
2. **Grounding model:** retrieval-grounded — the LLM never cites from recall. Every trait
   comes from a real source (a Wikidata record or fetched literature text), and every
   citation is tool-verified before the trait is admitted.
3. **`db`-tier source:** Wikidata via SPARQL (free, no key, stable Q-IDs as citations).
4. **`llm`-tier rigor:** extract-from-retrieved-text — the LLM may only emit a trait it can
   quote from text fetched for that taxon; the citation is that source.

## Architecture

New module **`app/trait_sources.py`** — pure core with injected network functions (mirrors
how `app/judge.py` / `app/traits.py` isolate the Anthropic client so units test without
network). It implements the two tiers; `build_trait_rubrics.py` delegates to them.

| Component                                                                 | Tier  | Responsibility                                                                                                                                                                         | Citation emitted                                  |
| ------------------------------------------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `wikidata_traits(taxon, *, sparql_fn)`                                    | `db`  | SPARQL the taxon's Wikidata item; map the morphology/phenology properties it exposes (growth habit, flower color, leaf arrangement, …) → trait dicts                                   | Wikidata Q-ID URL (resolvable; no paper to ghost) |
| `literature_grounded_traits(taxon, *, search_fn, resolve_fn, llm_client)` | `llm` | data-aggregator organism-aware search → resolve OA fulltext/abstract → LLM forced-tool extracts **only traits stated in the retrieved text, each with a verbatim quote** → trait dicts | source publication DOI/PMID                       |
| `verify_citations(traits, *, ghostcite_fn, resolve_fn)`                   | both  | paper citations → ghostcite (`--json`; byline+year+retraction); Wikidata IDs → resolve-check. Unverifiable ⇒ trait dropped                                                             | —                                                 |

**Anti-hallucination, two layers beyond ghostcite:**

1. Each extracted trait's `quote` MUST be a verbatim substring of the retrieved text, else the
   trait is dropped (catches an invented quote).
2. ghostcite confirms the citation's metadata is real and not retracted.

So the trait _claim_ comes from the source text, and the _citation_ is independently verified —
closing both halves of the ghost-cite gap (existence AND support).

## Trait dict schema

Unchanged fixed shape consumed by the judge + validator:
`key, trait_class, type, expected, visual, source_tier, citation`. The judge
(`app/traits.py:build_trait_messages`) reads only `key`, `trait_class`, `expected`.
`validate_trait` enforces all seven fields + non-empty citation + `trait_class ∈ SCORED_CLASSES`

- `source_tier ∈ {db, llm}`.

Two **provenance-only** fields are added inside `traits_json` (the judge ignores them; no
schema migration — `traits_json` is freeform JSON):

- `quote` — the verbatim supporting span from the source.
- `source_detail` — the Q-ID / DOI behind the citation.

`SCORED_CLASSES` = {habit, organ_shape, phyllotaxy, inflorescence, color, presence, proportion}.

## Data flow (per taxon, serial)

```
taxon name
  ├─ fetch_db_traits  → wikidata_traits → SPARQL Q-item → map props → [db traits, cite=Q-ID]
  ├─ draft_llm_traits → literature_grounded_traits:
  │       search_fn(organism=taxon, kind=publication) → top-N pubs
  │       → resolve_fn(OA) → fulltext/abstract text
  │       → llm extract (forced tool): {key, trait_class, expected, quote, doi}
  │       → keep only traits whose quote ⊂ retrieved text
  ├─ build_rubric_traits (existing fn — extended) returns validated traits:
  │       merge + dedup (same trait_class+expected across tiers → keep db, llm corroborates)
  │       → stamp source_tier
  │       → verify_citations: ghostcite(DOIs) + resolve(Q-IDs) → drop unverifiable
  │       → validate_trait (existing gate)
  └─ main() → upsert_rubric(taxon, task_id, traits)  (existing)
```

## Spend gate (operator discipline; API opt-in rule)

- `--dry-run` (no LLM, no spend): runs Wikidata + data-aggregator _search only_; reports per
  taxon — # db traits, # candidate pubs, # OA-resolvable, **estimated LLM extraction calls**
  (≈ pubs-to-read) + rough token/cost estimate. This count is brought to the user for an
  explicit go-ahead.
- `--live`: real extraction + ghostcite run. Spend happens only here, only after the user OKs
  the dry-run count. Snapshot the study DB before/after (incident rule).

## Failure handling (fail loud; never silently degrade)

- Wikidata/SPARQL error → raise (no `[]` masquerading as "no traits").
- data-aggregator returns zero pubs, or none OA → log loudly; that taxon's `llm` tier is empty
  (taxon may end `db`-only).
- A taxon ending with **zero usable traits → hard error, no empty rubric written** (an empty
  rubric would silently mark every output "fully covered" and skip judging — see
  trait_judge.py skip-fully-covered logic).
- ghostcite or required network unavailable → abort rather than admit unverified citations.
- Conservative serial pacing per taxon (web-scraping-safety rule); Wikidata + data-aggregator
  are the rate-bound hops.

## No new draft/review state

`TraitRubric` gets no manual-approval column (YAGNI). Trust is already gated downstream by the
κ-calibration before any class goes non-experimental, and every trait now carries `quote` +
verified `citation`, auditable via the existing `/api/traits.json`. A manual-approval state
would duplicate the calibration gate.

## Testing

Unit tests inject stubs (no network), plus a real-execution boundary probe.

- `wikidata_traits` — fake `sparql_fn` with a canned binding → well-formed `db` trait dicts;
  - a "no morphology props → `[]` (not error)" case.
- `literature_grounded_traits` — fake `search_fn`/`resolve_fn` (canned pubs + text blob) +
  forced-tool `llm_client` stub: (1) quote-in-blob trait kept; (2) quote-not-in-blob trait
  dropped; (3) dicts carry `source_tier="llm"` + DOI citation.
- `verify_citations` — fake ghostcite runner: verified DOI kept; unverified/retracted dropped;
  Q-ID resolve-fail dropped.
- Pipeline integration — `build_rubric_traits` with both injected tiers → real `validate_trait`
  → `upsert_rubric`; assert persisted `TraitRubric` with mixed-tier provenance.
- `--dry-run` smoke — search-only path with stubs; asserts counts printed + nothing written +
  no LLM client constructed.
- **Real-execution check** (operator, not pytest): `Solanum lycopersicum` through `--dry-run`
  live against actual Wikidata + data-aggregator, confirming SPARQL + search params return
  real bindings before any spend.

## Edit surface

- **Create** `app/trait_sources.py` (the 3 functions + SPARQL query constant + extraction tool
  schema + extraction prompt).
- **Modify** `scripts/build_trait_rubrics.py` — replace the two `NotImplementedError` bodies
  with delegations; insert `verify_citations` before `validate_trait`; extend `--dry-run` to the
  new counts/cost report.
- **Create** `tests/test_trait_sources.py`; extend `tests/test_build_trait_rubrics.py`
  (pipeline integration + dry-run smoke).
- **No changes** to `app/traits.py`, `app/models.py`, `app/service.py`, or any route — the
  judge/scoring side consumes whatever rubrics land.

**Dependencies:** `ghostcite` (installed at `~/miniconda3/bin/ghostcite`) invoked as a
subprocess with `--json`; data-aggregator (MCP) + Wikidata reached only under `--live` / real
`--dry-run` via the injected fns. No new Python packages for the unit-test path.

## Risks & mitigations

| Risk                                                               | Mitigation                                                                                   |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Real-but-irrelevant citation (paper exists, doesn't support trait) | Extract-from-retrieved-text: trait claim comes FROM the cited text; `quote ⊂ text` enforced. |
| LLM invents a quote                                                | Verbatim-substring check drops it.                                                           |
| Hallucinated/retracted citation                                    | ghostcite byline+year+retraction gate; unverifiable ⇒ dropped.                               |
| Wikidata thin on visual traits                                     | Acceptable — `llm` tier carries fine visual traits; `db` is the authoritative backbone.      |
| data-aggregator unavailable in headless/cron                       | Backends are `--live`-only and operator-run; not on any request path.                        |
| Empty rubric silently skips judging                                | Zero-trait taxon → hard error, no write.                                                     |

## Out of scope

- POWO/WFO/GBIF integration (Wikidata chosen for the `db` tier).
- Long-tail taxa beyond the 6 recon species (pipeline is taxon-agnostic, but only the 6 are
  validated/shipped here).
- AgriGen-side consumption (already loose-coupled via `/api/traits.json`).
- Running T7 itself (separate operator step after rubrics exist + dry-run approval).
