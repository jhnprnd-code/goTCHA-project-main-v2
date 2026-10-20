# goTCHA evaluation

Generated 2026-10-20 12:26 from backend\sessions_model_training.jsonl.

## Dataset

- Sessions used: 87 (27 human from 9 people; 60 bot from 20 bot runs)
- Skipped: {'practice': 30}
- Unscorable (fewer than 5 attempts, or timer step above 15.0 ms): {}
- Human groups: {'low_vision': 12, 'sighted': 12, 'prefer_not_to_say': 3} sessions
- Features: `default` (18), logistic regression C=0.3, target FRR 5%, threshold chosen inside each training fold

## Headline (deployed system = model + near-duplicate rule)

| | accepted | not accepted |
|---|---|---|
| human sessions | 27 | 0 |
| bot sessions | 36 | 24 |

- **FRR (strict, includes inconclusive):** 0.0% [0.0%, 29.9%]*
- FRR (model only, scorable sessions): 0.0% [0.0%, 29.9%]*
- **FAR (all bot sessions):** 60.0% [41.7%, 78.3%]
- FAR excluding test-only attackers: 62.5% [43.8%, 81.2%]
- AUC 0.720, EER 36.0% (optimistic: EER picks its threshold on test scores)
- Human passes within 3 attempts: 9/9 people (100%, 95% Wilson CI [70%, 100%])

CIs resample participants, not sessions. Humans contribute 3 correlated sessions each. A trailing * marks a rate of exactly 0% or 100%: nothing to resample, so a Wilson interval over participants is shown.

## FAR by attacker

| attacker | sessions | unscorable | FAR (model + dup rule) | FAR (model only) | AUC vs humans | note |
|---|---|---|---|---|---|---|
| pw_adaptive | 12 | 0 | 75.0% [50.0%, 100.0%] | 75.0% [50.0%, 100.0%] | 0.85 |  |
| pw_fitted | 12 | 0 | 91.7% [75.0%, 100.0%] | 91.7% [75.0%, 100.0%] | 0.65 |  |
| pw_gauss | 12 | 0 | 75.0% [50.0%, 100.0%] | 75.0% [50.0%, 100.0%] | 0.73 |  |
| pw_perfect | 12 | 0 | 8.3% [0.0%, 25.0%] | 83.3% [66.7%, 100.0%] | 0.78 |  |
| pw_replay | 12 | 0 | 50.0% [16.7%, 83.3%] | 100.0% [51.0%, 100.0%]* | 0.60 | test-only (copies humans; never trained on) |

## FRR by human group

| group | sessions | people | strict FRR |
|---|---|---|---|
| low_vision | 12 | 4 | 0.0% [0.0%, 49.0%]*  (fewer than 5 people: treat as a pilot) |
| prefer_not_to_say | 3 | 1 | 0.0% [0.0%, 79.3%]*  (fewer than 5 people: treat as a pilot) |
| sighted | 12 | 4 | 0.0% [0.0%, 49.0%]*  (fewer than 5 people: treat as a pilot) |

## Unseen attackers (retrained without that attacker)

| attacker held out | FAR | AUC vs humans |
|---|---|---|
| pw_adaptive | 83.3% [50.0%, 100.0%] | 0.75 |
| pw_fitted | 100.0% [51.0%, 100.0%]* | 0.35 |
| pw_gauss | 83.3% [66.7%, 100.0%] | 0.70 |
| pw_perfect | 8.3% [0.0%, 25.0%] | 0.22 |

## Which features carry the signal (AUC / EER, no threshold)

| feature set | AUC | EER |
|---|---|---|
| default | 0.720 | 36.0% |
| all | 0.876 | 21.9% |
| no_temporal | 0.728 | 38.5% |
| no_dwell | 0.528 | 49.9% |
| only_participation | 0.730 | 26.3% |
| only_location | 0.843 | 26.3% |
| only_scale | 0.648 | 37.7% |
| only_shape | 0.598 | 43.1% |
| only_temporal | 0.562 | 50.1% |

## Model comparison (default features)

| model | AUC | EER |
|---|---|---|
| logistic regression (L2) | 0.720 | 36.0% |
| SVM (RBF) | 0.748 | 26.3% |
| random forest | 0.740 | 28.1% |

## Timer precision (event times floored to a coarser clock)

| timer step | AUC with quantization correction | AUC without |
|---|---|---|
| native | 0.720 | 0.720 |
| 1 ms | 0.727 | 0.727 |
| 5 ms | 0.741 | 0.741 |
| 20 ms | 0.786 | 0.786 |
| 100 ms | 0.702 | 0.702 |

Above 15 ms the API refuses to judge (inconclusive) instead of guessing.

## Limits that belong in the paper

- Timing alone cannot stop an attacker who replays recordings of many different real people; the near-duplicate rule only catches reuse.
- Bot data comes from attackers we wrote. FAR is a lower bound for stronger attackers; the unseen-attacker table is the closest proxy for generalization.
- Few participants: every interval above is wide. Report intervals, not point estimates.
- Self-reported group labels; a 'low vision' row with few people is a pilot, not evidence of accessibility.
