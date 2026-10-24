"""Rule 15: every judgment answer must be at least as confident as the contract's
escalation threshold. A low-confidence answer doesn't mean something is wrong, so this
rule escalates to the approval key instead of holding (spec: "else escalate");
kernel/runner.py's ESCALATION_RULES treats it that way. With no Proposal (judgment
hasn't been wired in yet, or didn't run) there is nothing to check.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "15_confidence_floor"

QUESTION_WORDS = {
    "release_form_correct": "the lien release",
    "bank_change_suspicious": "the bank details",
    "progress_plausible": "the progress claimed",
    "duplicate_likelihood": "whether this is a duplicate",
    "proposed_action": "what to do overall",
}


def check(state: KernelState, request: PayRequest) -> RuleResult:
    threshold = state.contract.get("escalation_confidence_threshold")
    if state.proposal is None or threshold is None:
        return ok(RULE, {"checked": False}, "No automatic review ran on this request.")
    low = {q: c for q, c in state.proposal.confidences.items() if c < float(threshold)}
    evidence = {"threshold": threshold, "confidences": state.proposal.confidences}
    if low:
        about = ", ".join(QUESTION_WORDS.get(q, q) for q in low)
        return fail(RULE, {**evidence, "below_threshold": low},
                    f"The automatic review wasn't sure about {about}, so this needs your OK.")
    return ok(RULE, evidence, "The automatic review was confident on every question.")
