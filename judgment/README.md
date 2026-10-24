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

All five go in one request; Jev's limit is 8 questions and 32 KiB. Every number the model sees is computed in `facts.py`: matches, day counts, claimed-minus-video gaps and line similarity. Bank account numbers are masked to their last four digits, including inside email text. Email text is labelled as untrusted evidence in every question's instructions.

## Failure behavior

- Jev failures that trigger the fallback: a network error, an HTTP error (429/5xx are retried twice with backoff), an unexpected envelope, or any invalid answer. An invalid answer is a missing question, a choice that isn't an option, a confidence outside [0, 1], or a score outside its legend.
- The fallback is Claude structured output. It returns a probability per option, and code picks the answer and computes Score expectations, so the shape matches Jev's. Its confidences are self-reported, not calibrated, and the reasons say so.
- If both fail, `proposal` is `escalate` with every confidence at 0.0, so rule 15 sends the request to a person. `judge()` doesn't raise for model failures.

## Configuration

| env | default |
|---|---|
| `JEV_BASE_URL` | `https://ai-gateway.vercel.sh/typesafe` (Vercel AI Gateway) |
| `AI_GATEWAY_API_KEY` / `JEV_API_KEY` | key for the gateway / for direct `thejevai.com` |
| `JEV_MODEL` | `typesafe-ai/jev` via gateway, `typesafe/jev-1.14` direct |
| `ANTHROPIC_API_KEY` | used by the fallback |
| `JUDGMENT_FALLBACK_MODEL` / `JUDGMENT_FALLBACK_EFFORT` | `claude-opus-5-5` / `medium` |

## Integration notes

- **Proposal carries only confidences.** `Proposal` has `confidences` but not the answers themselves, while the spec feeds answers into rules 4, 10, 12 and 13. For example, rule 13 checks "Jev's plausibility answer". Today the answers reach those rules only through `JudgmentResult.answers`; a `Proposal.answers` field in `shared/schema.py` would carry them.
- **Rule 15 has no path to the Proposal.** It needs the `Proposal`, which `KernelState` doesn't hold and `decide()` doesn't take yet.
- **Wiring into the pipeline.** `pipeline.decide` can call `judge(ks, req)` before the kernel and log `result.facts` and `result.answers` as the `judgment` event. The calibration curve and conformal threshold need those logged answers.
