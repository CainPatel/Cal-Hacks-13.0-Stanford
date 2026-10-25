# Held-out test set (61 cases)

Written by Google Gemini, outside this codebase, from shared/schema.py, docs/kernel-input-contract.md and three
fixtures. Each case is `{"state": KernelState, "request": PayRequest, "label": {...}}` and runs straight through
`kernel.runner.decide`.

| File | Cases | Notes |
|---|---|---|
| batch_01.json | 10 | textbook attacks |
| batch_02.json | 15 | harder billing tricks; found 2 misses |
| batch_03.json | 15 | identity and payment tricks (several bank-swap variants) |
| batch_04.json | 21 | paperwork tricks; all variations of one base request, so less independent |

## Edits made after generation (recorded per case in `label.edits`)

No label or expected_action was changed. Only structural defects were fixed:

- `progress_tolerance_points: 5` removed from every contract (batches 1 and 4), so the real default of 10 applies.
- Batch 4 had no site video in any case (rule 13 held everything). Each case got a video dated 3 days before
  the period end, with estimates equal to the sub's printed % complete. That makes rule 13 neutral in batch 4:
  it can't catch or falsely hold anything there.
- Batch 4's conditional releases used a $500,000.00 placeholder; set to the request's payment due. Case 15
  (understated waiver) kept its deliberately short amount.

## Result against the current kernel

55 of 61 decided as labelled: 32 of 38 attacks held, 16 of 16 honest released, 7 of 7 escalations.

Got paid although labelled hold:
- batch 2 #8: approved change order for work already in the base scope.
- batch 2 #12: prior unconditional waiver covers less than was paid (rule 05 checks existence, not amount).
- batch 4 #20: change order approved after the period end still raises the ceiling (rule 02 ignores the date).
- batch 4 #2 and #13: billing against an unapproved change order while staying under the original contract.
  Debatable: no ceiling is exceeded; the inflated % would be rule 13's job, which the neutral video can't test.
- batch 4 #19: unconditional final waiver used for a progress payment. Debatable: a final waiver gives the GC
  more lien protection, not less.

Gemini's own review confidences (`proposal`) are included; removing them changes only batch 2 #14, which is
itself the low-confidence case.

Known kernel issue not triggered at the default tolerance: rule 13 measures billed % against the original
scheduled value and ignores approved change orders (batch 1 #3 passes by 0.7 points).
