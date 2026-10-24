# Draw: results

All numbers measured on `main`, Sat Oct 24, 2026 (~2 AM), unless a row says otherwise. Demo data is
synthetic (one fictional GC). Every figure has a command to reproduce it.

## Headline

| | |
|---|---|
| Attacks the rules stop on their own | **100%** of 8,029 (16 attack types, plus 1,244 two-fault combos) |
| Attacks paid with no human tap | **0** |
| Attacks paid even if the owner taps approve without reading | **0** |
| Honest requests wrongly held | **0** of 1,971 fuzzed, **0** of 720 replayed clean decisions (8 companies × 6 months) |
| Dollars in the attacks stopped | **$425.5M** (synthetic) |
| Owner's button presses, honest month | **1.83 per job per month** (63% of a clean month pays out with no tap) |
| Perception: synthetic pay apps read exactly | **940 / 940** (every field, three number styles, no Claude calls) |
| Jev: calibration, 6,000 real answers | 4 of 5 questions **100%** accurate; expected calibration error **0.029** |

## 1. Kernel vs. attacks (`make fuzz N=10000`)

Each attack type, 10,000 cases, rules 01-15:

| Attack | Tried | Caught by the intended rule | Paid with no tap |
|---|---|---|---|
| Bills more progress than the site video shows | 407 | 100% (13) | 0 |
| Inflates the contract value to hide over-billing | 437 | 100% (13) | 0 |
| Bills past the contract with no approved change order | 449 | 100% (02) | 0 |
| Withholds less retainage than the contract | 466 | 100% (03) | 0 |
| Pads the amount due on the summary page | 452 | 100% (01) | 0 |
| No lien release for this month | 509 | 100% (04) | 0 |
| Lien release covers less than the payment | 457 | 100% (04) | 0 |
| Last month's final waiver never came in | 455 | 100% (05) | 0 |
| Workers' comp has expired | 469 | 100% (06) | 0 |
| Supplier filed a notice and hasn't released | 466 | 100% (07) | 0 |
| Owner's draw hasn't cleared yet | 425 | 100% (08) | 0 |
| New sub whose tax ID doesn't match | 454 | 100% (09) | 0 |
| Email asks to pay a new bank account | 210 | 100% (10) | 0 |
| New bank account, from a lookalike domain | 212 | 100% (10, 11) | 0 |
| Resubmits last month's paid request | 464 | 100% (12) | 0 |
| Submits this month's request twice | 453 | 100% (12) | 0 |
| Two of the above at once (35 pairs) | 1,244 | 100% (both rules) | 0 |

### How we got here (each step found by the fuzzer or by hand probes)

| Kernel state | Rules alone catch | Paid with no tap |
|---|---|---|
| Rules 01-04 | 43% | 1,640 attacks ($27.4M) |
| Rules 01-07 | 65% | 636 ($11.2M) |
| + 2 new attacks (smart duplicate, missing prior waiver) | 64% | 864 ($14.7M) |
| Rules 01-15 | 100% | 0 |
| + 2 new attacks found by hand (inflated contract value, padded amount due) | 88% | 351 |
| + fixes to rules 01 and 13 | 100% | 0 |
| + 1 new attack found by hand (lien release for less than the payment) | 94% | 274 |
| + fix to rule 04 (**now**) | **100%** | **0** |

### Hand probes outside the fuzzer (all held by the right rule)
New bank account with no email (10) · lookalike domain using a Cyrillic "а" (10, 11) · same request
again with one line $1 higher (12) · unsigned release (04) · $1 release (04) · GC not additional
insured (06) · stored materials nobody can see on video (02, 13). **Policy, not a bug:** a sub can
claim up to 9 points over the video on every line (+$7,475 for one sub this month), because the
tolerance is 10.

## 2. The auto-release limit (`limit_sweep` in the scorecard)

| Limit | Taps per job per month | Clean month auto-paid | Attacks auto-paid |
|---|---|---|---|
| $10K | 4.79 | 4% | 0 |
| $15K | 3.54 | 29% | 0 |
| **$25K** | **1.83** | **63%** | 0 |
| $50K | 1.46 | 71% | 0 |
| $100K | 0.25 | 95% | 0 |

With all 15 rules, no limit lets an attack through; the limit only sets how often the owner taps.
(With rules 01-04 the same sweep went from $0.9M auto-paid at $10K to $90.8M at $100K.)

## 3. The money path (`tests/test_executor_gates.py`)

Each was a way money moved wrongly; each is now refused, with a test:

| Before | Now |
|---|---|
| Owner approves on the device, payment refused (rule 14 = the escalation) | Pays, with a valid signature |
| Owner signs $1.00, executor pays $105,564.60 | Signed amount, payee and bank account must match |
| Same approval pays twice | Each approval pays once (nonces from the log) |
| $9,172 escalation pays with no signature | Every escalation needs the owner's tap |
| A decision for another request pays this one | Refused |
| A decision with no rule results pays | All 15 rules must be present |
| Padded amount due is paid | Rule 01 recomputes it; executor checks it against the lines |
| Missing auto-release limit pays everything | Fails closed: escalates |

## 4. Perception (`make synthetic W=10 && make benchmark`)

- 940 synthetic document sets (G703/G702 PDF + XLSX, lien release, insurance certificate, emails), 4,700 files.
- **940 / 940** pay apps extracted with every field exact: all lines, all 8 G702 totals, application
  number, period, remit-to account. All from the PDF's text layer, no Claude calls.
- By number style: plain 351/351, accounting 337/337, bare 252/252.
- Caveat: clean PDFs from one template. Says nothing yet about scans or other contractors' layouts.

## 5. Judgment: Jev (TypeSafe API)

**Calibration** (`python -m fuzzer.calibration 1200`; details in `docs/calibration.md`):
6,000 real answers in 2 min 17 s.

| Question | Accuracy |
|---|---|
| Is the lien release the right form? | 100% |
| Does the bank change look like account takeover? | 100% |
| Is it a duplicate? | 100% |
| What should happen? | 100% (never released an attack, never held an honest request) |
| Is the claimed progress plausible? | 93.4% → **fix in PR #43** |

- **Finding:** every progress error was one attack. Jev was shown the % computed from the sub's
  printed contract value, so an inflated value fooled it (0/40). PR #43 computes it from the
  subcontract: **40/40**, with over-billing and clean cases still 40/40. Kernel rule 13 caught all
  of these regardless.
- **Pooled threshold** from the data: 0.93 (keeps 83% of answers; held-out error 0.32%).
- **October with Jev in the loop** (current `main`): Jev's proposal matched the kernel on **15/15**,
  lowest confidence 0.91, no extra button presses. (Before Jason's Oct 24 change, a 0.85 floor sent
  6 of 10 honest auto-releases to the button.)
- Speed: ~0.1-0.2 s per request (15 requests in 1.7 s).

## 6. The live deployment

- **drawweb.vercel.app** (dashboard) and **drawapi.vercel.app** (API). Neon Postgres event log
  (append-only by trigger), private Blob for uploads.
- Demo company loaded in 2.9 s (419 facts, 90 decisions). The live scorecard (2,000 attacks, scored on
  Vercel): 100% caught, 0 false holds.

## 7. The demo company

3 jobs · 15 subs · 51 contract lines · $5.82M in subcontracts · 90 pay requests (306 line items) over
May-October 2026 · October: 15 payments, $609,276. The three demo stories, each held by its own rule:
Coastal Concrete (07, supplier lien notice), Summit Drywall (13, 70% claimed vs 45% on video),
Brightline Electric (10 + 11, `brightIineelectric.com`).

## Tests

`make test`: 477 passed (6 skipped: Postgres tests need `TEST_DATABASE_URL`). Device: 5 passed.

## Reproduce

```
make test
make fuzz N=10000          # sections 1-2 → data/scorecard.json
make synthetic W=10        # section 4 documents
make benchmark             # section 4 accuracy
python -m fuzzer.calibration 1200   # section 5 (needs TYPESAFE_API_KEY + JEV_BASE_URL)
```
