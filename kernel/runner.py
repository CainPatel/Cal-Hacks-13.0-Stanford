"""Runs every rule in kernel/rules/, in order, and turns the results into a Decision.
Pure function of (KernelState, PayRequest) -> Decision; no I/O. The caller (api/ or a
script) is responsible for loading state and writing the Decision to events/.
"""
from decimal import Decimal

from kernel.rules import ALL_RULES
from kernel.state import KernelState
from shared.schema import Action, Decision, PayRequest, RuleResult


def is_new_vendor(state: KernelState) -> bool:
    return state.vendor.first_paid is None


def is_bank_change(state: KernelState, request: PayRequest) -> bool:
    return state.known_bank_account is not None and request.bank_account != state.known_bank_account


def is_over_limit(state: KernelState, request: PayRequest) -> bool:
    limit = state.contract.get("auto_release_limit_usd")
    if limit is None:
        return False
    return request.g702.current_payment_due > Decimal(str(limit))


# Rules whose failure means "a person should look", not "something is wrong": they escalate
# to the approval key instead of holding (spec, rule 15: "else escalate").
ESCALATE_ONLY = {"15_confidence_floor"}


def next_action_for(results: list[RuleResult]) -> str | None:
    """One concrete thing the office manager can do about the first failing rule."""
    failing = next((r for r in results if not r.passed and r.rule not in ESCALATE_ONLY), None)
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
        "09_vendor_verified": "Hold until vendor identity is verified.",
        "10_bank_details_unchanged": "Call the sub at the number on file to verify the new bank details.",
        "11_sender_domain_authentic": "Do not act on this email; call the sub directly to confirm.",
        "12_not_a_duplicate": "Hold; this looks like a resubmission of an already-paid request.",
        "13_progress_plausible": "Ask the super to re-verify progress on site.",
    }.get(failing.rule, "Hold for manual review.")


def decide(state: KernelState, request: PayRequest) -> Decision:
    results = [rule(state, request) for rule in ALL_RULES]
    holding = [r for r in results if not r.passed and r.rule not in ESCALATE_ONLY]
    unsure = any(not r.passed and r.rule in ESCALATE_ONLY for r in results)

    if holding:
        action = Action.hold
    elif unsure or is_over_limit(state, request) or is_new_vendor(state) or is_bank_change(state, request):
        action = Action.escalate
    else:
        action = Action.release

    return Decision(
        request_id=request.id,
        action=action,
        results=results,
        next_action=next_action_for(results) if action == Action.hold else None,
    )
