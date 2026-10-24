"""Rule 15: every judgment answer must clear the escalation confidence threshold.

Fails like any other rule when a confidence is too low, but kernel/runner.py routes a
failure here to escalate, not hold (spec: "confidence below the escalation threshold on
any Jev question forces escalation regardless of the proposed action"). See
runner.ESCALATION_RULES. No proposal at all (judgment hasn't been wired in yet) is
"nothing to check," not a failure -- but once judgment/judge.py runs and fails outright,
it returns a Proposal with 0.0 confidence everywhere (judgment/judge.py:_unavailable),
which this correctly fails.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "15_confidence_floor"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    if state.proposal is None:
        return ok(RULE, {"confidences": None, "threshold": None}, "No judgment available yet for this request.")

    threshold = state.contract.get("escalation_confidence_threshold", 0.85)
    low = {q: c for q, c in state.proposal.confidences.items() if c < threshold}
    evidence = {"confidences": state.proposal.confidences, "threshold": threshold, "below_threshold": low}
    if low:
        return fail(RULE, evidence, f"{len(low)} judgment answer(s) are below the confidence floor.")
    return ok(RULE, evidence, "Every judgment answer clears the confidence floor.")
