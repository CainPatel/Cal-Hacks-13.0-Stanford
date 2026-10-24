"""Runs every rule in kernel/rules/, in order, and turns the results into a Decision.
Pure function of (KernelState, PayRequest) -> Decision; no I/O. The caller (api/ or a
script) is responsible for loading state and writing the Decision to events/.
"""
from kernel.rules import ALL_RULES
from kernel.state import KernelState
from shared.schema import Action, Decision, PayRequest, RuleResult

# A failure here means "this needs the owner's physical sign-off," not "something is
# wrong" -- so it routes to escalate instead of hold. Every other rule's failure holds.
ESCALATION_RULES = {"14_within_spend_authority", "15_confidence_floor"}


def is_new_vendor(state: KernelState) -> bool:
    return state.vendor.first_paid is None


def is_bank_change(state: KernelState, request: PayRequest) -> bool:
    return state.known_bank_account is not None and request.bank_account != state.known_bank_account


def next_action_for(results: list[RuleResult]) -> str | None:
    """One concrete thing the office manager can do about the first failing rule."""
    failing = next((r for r in results if not r.passed and r.rule not in ESCALATION_RULES), None)
    if failing is None:
        return None
    return {
        "01_cumulative_continuity": "Ask the sub to resubmit starting from our certified total.",
        "02_scheduled_value_ceiling": "Ask the sub for an approved change order or reduce the request.",
        "03_retainage_math": "Ask the sub to correct retainage to the contract rate.",
        "04_conditional_release_present": "Ask the sub for a signed conditional release.",
        "05_prior_unconditional_received": "Request last month's unconditional waiver.",
        "06_insurance_current": "Request an updated certificate of insurance.",
        "07_lower_tier_released": "Ask the sub for their supplier's release.",
        "08_owner_draw_funded": "Wait until the owner's draw for this period clears.",
        "09_vendor_verified": "Hold until vendor identity is verified.",
        "10_bank_details_unchanged": "Call the sub at the number on file to verify the new bank details.",
        "11_sender_domain_authentic": "Do not act on this email; call the sub directly to confirm.",
        "12_not_a_duplicate": "Hold; this looks like a resubmission of an already-paid request.",
        "13_progress_plausible": "Ask the super to re-verify progress on site.",
    }.get(failing.rule, "Hold for manual review.")


def decide(state: KernelState, request: PayRequest) -> Decision:
    results = [rule(state, request) for rule in ALL_RULES]
    hard_fails = [r for r in results if not r.passed and r.rule not in ESCALATION_RULES]
    escalation_fails = [r for r in results if not r.passed and r.rule in ESCALATION_RULES]

    if hard_fails:
        action = Action.hold
    elif escalation_fails or is_new_vendor(state) or is_bank_change(state, request):
        action = Action.escalate
    else:
        action = Action.release

    return Decision(
        request_id=request.id,
        action=action,
        results=results,
        next_action=next_action_for(results) if action == Action.hold else None,
    )
