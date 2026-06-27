# VLM ↔ Human Calibration Report

## Criterion: overall

| view | κ (human vs VLM) | n | self-consistency flip-rate | n_groups | rank ρ |
|---|---|---|---|---|---|
| single | 0.267 | 39 | 0.120 | 50 | N/A (full-grid multi4 only) |
| multi4 | 0.226 | 39 | 0.140 | 50 | nan |
| turntable | 0.259 | 39 | 0.120 | 50 | N/A (full-grid multi4 only) |

Rank correlation is computed only for multi4 (the full-grid leaderboard condition); single/turntable are evaluated on the calibration subset only, so they have no comparable full-grid leaderboard.

## Criterion: visual_quality

| view | κ (human vs VLM) | n | self-consistency flip-rate | n_groups | rank ρ |
|---|---|---|---|---|---|
| single | 0.153 | 38 | 0.160 | 50 | N/A (full-grid multi4 only) |
| multi4 | 0.116 | 39 | 0.120 | 50 | nan |
| turntable | 0.059 | 38 | 0.140 | 50 | N/A (full-grid multi4 only) |

Rank correlation is computed only for multi4 (the full-grid leaderboard condition); single/turntable are evaluated on the calibration subset only, so they have no comparable full-grid leaderboard.

## Criterion: structural_accuracy

| view | κ (human vs VLM) | n | self-consistency flip-rate | n_groups | rank ρ |
|---|---|---|---|---|---|
| single | 0.490 | 28 | 0.180 | 50 | N/A (full-grid multi4 only) |
| multi4 | 0.588 | 29 | 0.060 | 50 | nan |
| turntable | 0.588 | 29 | 0.080 | 50 | N/A (full-grid multi4 only) |

Rank correlation is computed only for multi4 (the full-grid leaderboard condition); single/turntable are evaluated on the calibration subset only, so they have no comparable full-grid leaderboard.
