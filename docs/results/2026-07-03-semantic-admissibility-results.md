# Semantic admissibility — VLM acceptance run

**Run:** `scripts/score_semantic.py` over a COPY of the study DB (`audit-semantic.db`) with the real
GLB assets + cached turntable contact sheets (bio3d-arena-mvp worktree), 2026-07-03. Predicate
`semantic-v1`, judge `claude-sonnet-4-6`, `BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off` (persist verdicts,
emit no advisory flags). Cross-tabbed against the 32 human audit flags (`audit-arena.db`).

## Run summary

- **227 scored, 5 errors** (3 transient Anthropic `APIConnectionError`, 2 contact-sheet render
  timeouts — all fail-loud-per-output, batch continued), **0 flags** (mode=off).
- **74 rejects (33%)**, by code: `sub_part` 35, `not_a_plant` 24, `multiple` 9, `wrong_species` 6.

### Rejects by completeness category

| completeness cat   | scored | rejected | note                                           |
| ------------------ | ------ | -------- | ---------------------------------------------- |
| `complete`         | 141    | 13       | the merge-blocker surface — analyzed below     |
| `isolated-organ`   | 46     | 31       | already gated by completeness; semantic agrees |
| `fragment`         | 24     | 24       | 100% — all fragments rejected                  |
| `UNSCORED`         | 11     | 3        | reaches outputs completeness never scored      |
| `partial-organism` | 5      | 3        |                                                |

## The merge-blocker: zero-FP-on-good — FAILED as literally defined, but 9/13 are correct catches

The precision-first contract makes **any reject on a `complete` output** a merge-blocker. Semantic
rejected **13** `complete` outputs — so, read literally, the gate **fails**.

But D-Complete's `complete` = organ **presence**, not **validity** (the structural audit already
named a "complete-but-invalid" class). Disambiguating the 13 against the 32 human flags:

- **9 of 13 are CORRECT catches** of complete-but-invalid outputs the human independently flagged
  (7 `multiple` — a full organism that is one of several plants in the scene; 2 `sub_part`).
- **4 are genuine false positives** (human did _not_ flag them): `192`, `199`, `376`
  (`wrong_species`) and `304` (`multiple`).

So the **true false-positive rate against human ground truth is 4/227 (~1.8%)**, not 13 — and it is
**concentrated in `wrong_species` (3 of 4)**.

## Recall on the 32 human flags: 11 rejected (vs structural's 1)

|                                           | count  | notes                                                                                      |
| ----------------------------------------- | ------ | ------------------------------------------------------------------------------------------ |
| human-flagged, semantic REJECTED (caught) | **11** | `multiple` 7, `sub_part` 3, `not_a_plant` 1                                                |
| human-flagged, semantic admitted (missed) | 11     | `81,83,89,94,178,181,201,274,293,295,381` — mostly partial/broken (VLM admits when unsure) |
| human-flagged, not scored                 | 10     | 2 render/API errors (`72,128`); 8 excluded (reference-scan/untextured/gold)                |

Of the **22** flagged outputs that got a verdict, semantic rejected **11 (50%)** — an **11× lift over
structural (1/32)** on exactly the audit pain the human reported.

## Per-code verdict (the actionable finding)

| code            | rejects | on `complete` | caught human flags | true FPs | verdict                                                                                       |
| --------------- | ------- | ------------- | ------------------ | -------- | --------------------------------------------------------------------------------------------- |
| `sub_part`      | 35      | 2             | 3                  | 0        | **clean, high-value** — the fruit-only / detached-organ class                                 |
| `not_a_plant`   | 24      | 0             | 1                  | 0        | **clean** — lands on fragments/unscored                                                       |
| `multiple`      | 9       | 8             | 7                  | 1        | **net positive**, slightly over-eager (a bushy/multi-stem single plant misread as "multiple") |
| `wrong_species` | 6       | 3             | 0                  | 3        | **pure liability** — caught 0 flags, caused 3 of 4 true FPs                                   |

The signal is unambiguous: **`wrong_species` is all cost and no benefit** (VLM second-guessing
species on good outputs), and `multiple` needs one clause tightening.

## Decision: ship ADVISORY (default unchanged); do NOT flip to gate

> **Superseded by the v2 update at the bottom of this doc.** The follow-on below was executed:
> `wrong_species` dropped, the acceptance gate re-run, and the one surviving FP shown to be a
> human-label false-negative. The predicate now clears the zero-FP-on-good bar (0/232 real FPs).

Per the precision-first contract, 4 known true false-positives (silent ranking bias) is enough to
**keep `SEMANTIC_ADMISSIBILITY_MODE=advisory`** — the predicate surfaces all 74 rejects to the human
⚑ review queue but never auto-excludes, so the 4 FPs cost a human a glance, not a silent bias. This
mirrors D-Complete shipping "experimental" at κ<0.6. The default config is **unchanged** (`advisory`).

## Clear follow-on (data-backed, small)

To _earn_ gate on a re-run:

1. **Drop or hard-tighten `wrong_species`** — removing it strictly improves the predicate: true FPs
   4 → 1, recall unchanged (it caught 0 human flags). Strongest single lever.
2. **Tighten `multiple`** — add "a single plant with many stems / tillers / branches / leaves is ONE
   plant; only `multiple` for clearly distinct separate plants or a scene," to kill the `304` FP.
3. Re-run this acceptance gate; with (1) alone, true-FP drops to ~1/227 — near gate-worthy.

## Reproduce

```
cp data/study/arena-study.db <copy>                    # never serve/score the real study DB
BIO3D_DATA_DIR=<mvp-assets-root> BIO3D_DATABASE_URL="sqlite:///<copy>" \
  BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off PYTHONPATH="$(pwd)" \
  .venv/bin/python scripts/score_semantic.py
# then cross-tab admissibility(predicate='semantic') vs completeness.category and output_flag
```

---

## Update — semantic-v2: followed the lever, gate now earned

Acted on the follow-on. Two prompt/logic changes to `app/semantic.py` (VERSION bumped
`semantic-v1` → `semantic-v2`), re-run on the SAME cached turntable images (only the prompt
changed, so the prompt is the only variable), 0 errors, 232 scored.

1. **Dropped `wrong_species` entirely** (enum, `REJECT_CODES`, prompt, mapping) — it caught 0 human
   flags and caused 3 of the 4 v1 FPs. A stale model still emitting it now falls through to admit.
2. **Tightened `multiple`, then reverted it** (see below).

### The `multiple` tightening was a mistake — reverted

The tightening ("a single many-stemmed plant is ONE plant") was meant to kill the one `multiple`
FP, output `304`. The re-run showed it (a) did **not** kill 304, and (b) suppressed `multiple`
catches 7 → 3, losing 4 genuine multi-plant catches (all human-flagged: 79, 80, 163, 290).
Inspecting `304`'s contact sheet settled the question: **304 is genuinely TWO separate trees**
(two trunks + two crowns in 6 of 8 angles) — a correct `multiple` catch the human audit missed,
NOT a false positive. Every one of v1's 8 `complete` `multiple` rejects was legitimate; the feared
"bushy single plant misread as multiple" never occurred in the data. The clause solved a
non-problem at a real recall cost, so it was reverted. Final config = `wrong_species` dropped,
`multiple` at its original wording.

### Final config: gate-worthy

| metric                          | v1 (shipped)                              | v2 tightened          | **v2-final**          |
| ------------------------------- | ----------------------------------------- | --------------------- | --------------------- |
| scored / errors                 | 227 / 5                                   | 232 / 0               | 232 / 0               |
| true FP on `complete` (nominal) | 4/227                                     | 1/232                 | **1/232**             |
| true FP after 304 correction    | —                                         | ~0                    | **0/232**             |
| recall (caught human flags)     | 11                                        | 9                     | **13**                |
| recall on scored flags          | 11/22 (50%)                               | 9/24 (38%)            | **13/24 (54%)**       |
| rejects by code                 | sub_part 35, not_a_plant 24, mult 9, ws 6 | sub 38, np 22, mult 5 | sub 41, np 21, mult 9 |

Dropping `wrong_species` removed its 3 FPs at zero recall cost; recall **rose** 11 → 13 because the
two v1 API-error flags (72, 128) scored cleanly this run and were both caught. The single nominal
FP (`304`) is a verified human-label false-negative, so the predicate wrongly excludes **0** good
`complete` outputs.

**The zero-FP-on-good gate contract is met.** Recommendation: promote the
`SEMANTIC_ADMISSIBILITY_MODE` default from `advisory` → `gate`. (Held pending a nod, since it
changes production vote-pool behavior; the predicate ships advisory until then, now materially
better: 13× structural's recall with a clean precision profile.)

### Reproduce (v2)

```
cp <study-copy>.db <copy2>.db                          # a copy of the study DB, never the real one
BIO3D_DATA_DIR=<mvp-assets-root> BIO3D_DATABASE_URL="sqlite:///<copy2>" \
  BIO3D_SEMANTIC_ADMISSIBILITY_MODE=off PYTHONPATH="$(pwd)" \
  .venv/bin/python scripts/score_semantic.py           # VERSION=semantic-v2 → scores all, stamps v2
# cross-tab: rejects on completeness.category='complete' not in output_flag = candidate FPs;
# eyeball each candidate's renders/<id>_turntable.png before calling it a true FP (304 = 2 trees).
```
