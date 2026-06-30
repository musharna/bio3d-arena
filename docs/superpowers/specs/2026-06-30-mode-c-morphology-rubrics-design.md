# Mode-C Morphology Rubrics — Design Spec

> Created 2026-06-30. Parent: Mode-C botanical-trait ground truth.
> Supersedes the literature-extraction trait set for the 6 study taxa.

## Problem

The literature-derived rubrics (90 traits from Europe PMC extraction) are unfit for
visual judging of static 3D models. A human-vs-VLM audit on 77 labels showed 47/77
disagreement, and a judgeability screen found only ~17/90 traits are genuinely visible
on a render of one normal specimen. The failures are systematic, not noise:

- **Genetics/mutant abstractions** ("floral organ identity", "B-function identity")
- **Temporal** ("flowering time early", "accelerated floral transition", "degreening during maturation")
- **Comparative without a baseline** ("altered", "reduced", "thickened", "smaller")
- **Internal/microscopic** (ovary, carpel number, seeds-per-fruit, stomata, abscission zone)
- **Wrong organism** (commelina/poaceae traits in maize, arabidopsis sepals in rose,
  cannabis trichome in tomato, stone-tool "flake circularity" in maize)

Root cause: the **source** was wrong. Research abstracts describe what changed in an
experiment, not what a normal plant of the species looks like. No downstream filter fixes
that. The fix is a source pivot to **descriptive morphology**, plus a judgeability gate
that blocks bad traits at authoring time.

## Goal

Replace the 6 study taxa's rubrics with hand-authored, Wikidata-grounded, **visually
judgeable** morphology traits (~8–12 per taxon), re-judge, and recalibrate — so Mode-C
botanical-accuracy scores become trustworthy rather than merely unblocked.

## Decisions (locked in brainstorming)

1. **Grounding = Wikidata-first + cited gaps.** Pull from Wikidata structured morphology
   where present; fill remaining visible traits from a cited botanical reference per trait.
2. **Transition = clean slate.** Replace old traits per taxon; re-run `trait_judge`; reset
   labeling to the new sample; recalibrate. Existing 2260 verdicts + in-progress human
   labels are discarded (they target dead traits). Snapshot the study DB first.
3. Scope = the existing 6 taxa: Solanum lycopersicum, Zea mays, Pinus sylvestris, Rosa,
   Glycine max, Arabidopsis thaliana.

## A. The trait standard (`is_visually_judgeable`)

A reusable validator. A trait is admissible **iff all** hold; otherwise it is rejected at
authoring time (and the rejection reason is logged — no silent drops):

1. **Static** — names a shape/color/structure present at a single moment. Reject
   time-course / process language: `altered, change, transition, accelerated, delayed,
recurrence, maturation, ripening-process, senescence, flowering time, bud stage`.
2. **Macroscopic & external** — visible on a whole-plant multi-view render. Reject
   `stomat*, trichome, glandular, multicellular, cellular, epiderm*, ovary, carpel,
seed coat, seeds-per-*, abscission, pollen, meristem`.
3. **Absolute** — an expected value checkable on ONE specimen. Reject bare comparatives
   with no in-trait referent: `increased, reduced, smaller, larger, thickened, prolonged,
superior, affected, malformed (alone), variable depending on, substantial variation`.
4. **Correct taxon** — reject other-genus/family/mutant-line tokens not matching the
   rubric taxon: `commelina, poaceae, arabidopsis (outside its own rubric), cannabis,
flake, circularity, lithic`, named mutant alleles.
5. **Concrete** — names a specific appearance. Reject vacuous: `diversified, highly
complex, complexity, architecture (alone), patterning defect, disruption`.
6. **Has a value** — non-empty `expected`, not `not explicitly stated / unknown / n/a`.

The audit's failure examples become the validator's test fixtures (see Testing).

This validator is the "harden" deliverable: it runs in the authoring path AND is wired
into `scripts/build_trait_rubrics.py` so any future literature re-run is gated too.

## B. Schema (unchanged — downstream untouched)

Each trait is a dict: `{key, trait_class, type, expected, visual, source_tier, citation}`,
stored in `TraitRubric.traits_json`. `trait_class` ∈ the 7 SCORED*CLASSES
(`habit, organ_shape, phyllotaxy, inflorescence, color, presence, proportion`). Verdict
vocab (`present_correct|present_wrong|absent|not_assessable`) and all of
`trait_judge` / `calibration_labels` / `label_server` / `service.recompute*\*` are unchanged.

- `source_tier = "db"` when the value came from a Wikidata property; `"ref"` when from a
  cited botanical reference.
- `citation` = the Wikidata Q-ID URL (db tier) or the reference DOI/URL (ref tier).
- `expected` = the absolute value to check (e.g. "red berry", "alternate", "climber").
- `visual` = a short note on what to look for in the render (free text, judge guidance).

## C. Per-taxon trait sets (~8–12 each; balanced for per-class calibration)

Calibration accepts a **trait_class** at κ≥0.6 & n≥20, pooled across taxa. So each
applicable class needs ≥1 solid trait in enough taxa that its verdict pool exceeds the
sample size (~26 outputs/taxon × 1 trait = ~26 verdicts/class/taxon — ample). Target:
cover all 7 classes across the taxon set, every taxon contributing where the class applies.

Representative (final values authored + grounded at implementation, each validator-passing
and cited):

- **Solanum lycopersicum** — habit (sprawling/decumbent), organ_shape: leaf (pinnately
  compound), organ_shape: fruit (globose), color: flower (yellow), color: fruit (red),
  inflorescence (cyme), phyllotaxy (alternate), presence (fruit present), proportion
  (fruit small-to-medium).
- **Zea mays** — habit (tall single-culm grass), organ_shape: leaf (long linear),
  phyllotaxy (alternate/distichous), inflorescence (terminal tassel), presence (ear /
  prop roots), color (green foliage), proportion (tall).
- **Pinus sylvestris** _(gymnosperm — no flowers/fruit)_ — habit (excurrent conifer),
  organ_shape: leaf (needle), phyllotaxy (needles in fascicles of 2), organ_shape: cone
  (ovoid woody cone), presence (cones), color (blue-green foliage / orange upper bark),
  proportion (tall). `inflorescence` and flower-`color` have no pine trait (the classes
  still calibrate from angiosperm taxa).
- **Rosa** — habit (shrub/climber), organ_shape: leaf (odd-pinnate, serrate),
  organ_shape: fruit (hip), color: flower, inflorescence (corymb/solitary), presence
  (prickles), phyllotaxy (alternate), proportion.
- **Glycine max** — habit (erect herbaceous annual), organ_shape: leaf (trifoliate),
  inflorescence (axillary raceme), presence (pods), color (flower white/purple),
  phyllotaxy (alternate), proportion (pod/plant size).
- **Arabidopsis thaliana** — habit (rosette + erect inflorescence stalk), organ_shape:
  leaf (spatulate rosette), inflorescence (raceme), color (flower white), organ_shape:
  fruit (silique), presence (siliques), phyllotaxy (rosette/spiral), proportion (small).

## D. Architecture & transition

New code:

- `app/trait_morphology.py` (or extend `trait_sources.py`): `is_visually_judgeable(trait)`
  validator + the authored per-taxon trait definitions (grounded: Wikidata fetch for db-tier
  values, cited references for ref-tier), and a `build_morphology_rubric(taxon, *, fetch_db)`
  that assembles + validates + stamps citations.
- `scripts/author_morphology_rubrics.py`: load the validated rubrics into the study DB,
  replacing existing `TraitRubric` rows for the 6 tasks. `--dry-run` prints the per-taxon
  trait table + any rejected traits; `--commit` writes (after a DB snapshot).

Reused unchanged: `scripts/trait_judge.py` (re-judge), `scripts/label_server.py` +
`scripts/calibration_labels.py` (relabel/export/ingest), `app/service.recompute_trait_*`
(calibrate/score).

Harden: wire `is_visually_judgeable` into `build_trait_rubrics.py` so the literature path
rejects non-judgeable traits too (defense in depth; the morphology authoring is the primary
source now).

Transition runbook (operator-gated, after spec→plan→implement):

1. Snapshot `data/study/arena-study.db`.
2. `author_morphology_rubrics.py --commit` → replaces 6 rubrics.
3. `trait_judge` full pass on new traits (VLM spend ~$3–5) → fresh verdicts.
4. Reset `calibration_labels_filled.csv`; regenerate the labeling sample; restart `label_server`.
5. Human labels → `calibration_labels.py ingest --commit` → calibrate → scores light up.

## Testing

- `is_visually_judgeable`: each audit failure category is a rejection test
  (genetics/temporal/comparative/microscopic/wrong-taxon/vague/empty) + positive cases
  ("red berry", "alternate", "climber", "trifoliate leaf").
- `build_morphology_rubric`: every authored trait passes the validator; every trait has a
  non-empty citation; db-tier values come from the injected Wikidata fetch (mockable);
  per-taxon count in range; pine has no flower/fruit traits.
- Real-execution: dry-run the authoring script against the study DB (read-only) and inspect
  the per-taxon table before any commit.

## Risks / open items

- **Pine coverage thin** — gymnosperm lacks flower/fruit/inflorescence; accepted (classes
  pool across taxa). If a class ends up <20 labelable, it simply stays experimental.
- **Wikidata sparsity** — most morphology will be ref-tier (cited reference), not db-tier;
  acceptable, the citation requirement still holds.
- **Re-judge + relabel cost** — one VLM pass (~$3–5) + human re-labeling; the price of a
  trustworthy axis. Operator-gated.
- The current human labels (5–77 depending on progress) are discarded; backed up in job tmp.
