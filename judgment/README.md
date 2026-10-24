# judgment

`judge(state, request) -> JudgmentResult`. `state` is the kernel's per-request slice (`kernel.state.KernelState`, or any object with the same attributes). `JudgmentResult` holds:

- `proposal`: the `Proposal` (`action`, `confidences` keyed by question, plain-English `reasons`).
- `source`: `"jev"`, `"claude"` (fallback), or `"unavailable"`.
- `answers`: each question's answer, with Score answers normalized to 0–1, plus its confidence and probabilities.
- `facts`: the exact state sent to the model. This is what belongs in the event log's `judgment` event.
- `errors`: why an earlier source was skipped.

```
state slice ─► facts.py (all arithmetic, in code) ─► Jev System One ─► validate ─► Proposal
                                                        │ error / invalid
                                                        └─► Claude fallback ─► validate ─► Proposal
                                                               │ error / invalid
                                                               └─► escalate, confidence 0 everywhere
```

## Questions

These are the five questions from the spec's judgment-layer table, defined in `questions.py`:

| id | type | answer |
|---|---|---|
| `release_form_correct` | Choice | correct / wrong_type / incomplete |
| `bank_change_suspicious` | Score | 0–1 (5 levels, low to high) |
| `progress_plausible` | Choice | plausible / overstated / understated |
| `duplicate_likelihood` | Score | 0–1 (5 levels) |
| `proposed_action` | Choice | release / hold / escalate |

All five go in one request; Jev's limit is 8 questions and 32 KiB.

- **Facts come from the money, not the sub's summary.** Every number the model sees is computed in `facts.py`. The payment due is recomputed as billed to date, less retainage at the contract rate, less what the ledger shows was paid. Progress is what the dollars bill (`total_to_date ÷ scheduled_value`), not the sub's % column. Where the sub's own figure differs, it is given alongside with a flag.
- **The overall question sees the kernel's inputs.** `contract` facts summarize what the other kernel checks look at, so `proposed_action` sees it too: short retainage, lines past scheduled value plus approved COs, expired insurance, unreleased supplier notices, and whether the owner draw has arrived.
- **Duplicates use a tolerance.** Line totals within 1% count as the same, so a resubmission nudged by a cent still matches.
- **Bank-change emails come first.** They are listed ahead of routine mail, with counts, so routine mail can't crowd them out.
- **Imitation domains name the trick.** A sender domain that imitates the vendor's says how: confusable characters, non-ASCII letters, a hyphen, a changed TLD, the vendor's name in a subdomain, or one or two letters changed.
- **Account numbers are masked.** Anything account-like is masked to its last four digits, however it's written, in all free text.
- **Copied text is untrusted.** Text copied from documents or emails is labelled as untrusted in every question's instructions.

## Failure behavior

- **Jev failures that trigger the fallback:**
  - network errors, including dropped connections and truncated reads
  - HTTP errors (429 and 5xx are retried twice with backoff)
  - redirects, which are refused so the key can't follow them to another host
  - a body that isn't JSON or isn't a known envelope
  - any invalid answer: a missing question, a choice that isn't an option or disagrees with its own probabilities, probabilities outside [0, 1] or not summing to 1, a confidence outside [0, 1], or a score that isn't a number in its legend. A legend that echoes the level texts must keep them in the declared order.
- **The fallback is Claude structured output.** It must give a probability in [0, 1] for every option, summing to 1, and nothing is renormalized toward certainty. Code picks the answer and computes Score expectations, so the shape matches Jev's. Its confidences are self-reported, not calibrated, and the reasons say so.
- **If both fail,** or the facts can't be built, `proposal` is `escalate` with every confidence at 0.0, so rule 15 sends the request to a person. `judge()` doesn't raise.

## Configuration

| env | default |
|---|---|
| `JEV_BASE_URL` | `https://ai-gateway.vercel.sh/typesafe` (Vercel AI Gateway) |
| `JEV_API_KEY` (or `AI_GATEWAY_API_KEY`) | Jev key. Via the gateway either name works, with `AI_GATEWAY_API_KEY` taking precedence; direct `thejevai.com` reads `JEV_API_KEY` |
| `JEV_MODEL` | `typesafe-ai/jev` via gateway, `typesafe/jev-1.14` direct |
| `ANTHROPIC_API_KEY` | used by the fallback |
| `JUDGMENT_FALLBACK_MODEL` / `JUDGMENT_FALLBACK_EFFORT` | `claude-opus-5-5` / `medium` |

## Integration notes

- **Proposal carries only confidences.** `Proposal` has `confidences` but not the answers themselves, while the spec feeds answers into rules 4, 10, 12 and 13. For example, rule 13 checks "Jev's plausibility answer". Today the answers reach those rules only through `JudgmentResult.answers`; a `Proposal.answers` field in `shared/schema.py` would carry them.
- **Rule 15 has no path to the Proposal.** It needs the `Proposal`, which `KernelState` doesn't hold and `decide()` doesn't take yet.
- **Wiring into the pipeline.** `pipeline.decide` can call `judge(ks, req)` before the kernel and log `result.facts` and `result.answers` as the `judgment` event. The calibration curve and conformal threshold need those logged answers.
