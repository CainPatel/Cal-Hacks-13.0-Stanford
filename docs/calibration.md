# Jev calibration (first real run)

Run: Oct 24, 2026, 1:30 AM. TypeSafe's API (`JEV_BASE_URL=https://api.typesafe.ai`), Jev only (no Claude
fallback), 1,200 fuzzed cases (16 attack types plus clean requests) → **6,000 answers** in 2 min 17 s.
Reproduce: `python -m fuzzer.calibration 1200` with the key in the environment. Writes
`data/calibration.json` (served at `GET /calibration`) and `data/calibration_points.json` (raw answers).

## Results

| Question | Answers | Accuracy | Notes |
|---|---|---|---|
| `release_form_correct` | 1,200 | **100%** | |
| `bank_change_suspicious` | 1,200 | **100%** | |
| `duplicate_likelihood` | 1,200 | **100%** | |
| `proposed_action` | 1,200 | **100%** | Never released an attack, never held an honest request |
| `progress_plausible` | 1,200 | **93.4%** | 79 wrong, some at 0.97-1.00 confidence |

- **Calibration overall:** expected calibration error **0.029**. Stated confidence tracks accuracy closely.
- **Pooled threshold** (all questions, Clopper-Pearson, Bonferroni over 50 cuts, chosen on half and checked on the other):
  **0.93**. That keeps 83% of answers, and the held-out error is **0.32%** against a ≤1% target.
- **Per question,** no cut is certified yet. For the four perfect questions that's sample size, not accuracy:
  certifying ≤1% at 95% with zero errors needs about 690 error-free answers per half, and each half had 600.
  About 1,500 cases would certify them.

## The one problem: progress, on one attack

A targeted re-run of 40 cases each:

| Case | Jev's progress answer right |
|---|---|
| clean | 40 / 40 |
| `overbill_pct` (claims more than the video) | 40 / 40 |
| `inflated_scheduled_value` (sub prints a bigger contract value so its % matches the video) | **0 / 40** |

Every progress error is that attack. The cause is in the judgment inputs: `judgment/facts.py`
`_billed_pct` divides by the **sub's printed** scheduled value. Rule 13 uses the contract's, so the
kernel still holds every one of these cases, and no money is at risk.
**Fix (C):** compute billed % from `state.sov_scheduled_value(item_no)`, as rule 13 does.

## What it means for rule 15 (the confidence floor)

- On honest October requests, Jev answered `progress_plausible` correctly but with confidence 0.56-0.82.
  At the 0.85 floor, that sent **6 of 10** clean auto-releases to the button (2 → 8 presses a month).
- The progress question is **underconfident**: answers it rates 0.7-0.8 are right 94% of the time.
- Its real errors happen at 0.97-1.00 confidence, so a floor can't catch them. On this question the
  floor only adds false escalations.

**Recommendation (A, C):**
1. Fix `_billed_pct` (above), then re-run with ~1,500 cases to certify per-question cuts.
2. Until then, don't apply rule 15's floor to `progress_plausible`; rule 13 checks progress against
   the video deterministically. Keep the floor on the other four questions.
