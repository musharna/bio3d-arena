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
