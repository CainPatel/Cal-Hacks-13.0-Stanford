"""Rule 14: is this amount within what the contract lets the system pay on its own.

This rule fails like any other when the amount is over the limit, but kernel/runner.py
routes a failure here to escalate (the ESP32), not hold -- an over-limit amount isn't
wrong, it just needs the owner's physical sign-off. See runner.ESCALATION_RULES.
"""
from decimal import Decimal

from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "14_within_spend_authority"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    limit = state.contract.get("auto_release_limit_usd")
    amount = request.g702.current_payment_due
    if limit is None:
        # Fail closed: a missing limit must not mean "no limit". This fails like an
        # over-limit amount, so it escalates everything rather than quietly releasing it.
        return fail(RULE, {"limit": None, "amount": str(amount)},
                     "No auto-release limit is configured; this needs the owner's sign-off.")

    limit_dec = Decimal(str(limit))
    evidence = {"amount": str(amount), "limit": str(limit_dec)}
    if amount > limit_dec:
        return fail(RULE, evidence, f"${amount} is over the ${limit_dec} auto-release limit; needs the owner's sign-off.")
    return ok(RULE, evidence, "Within the auto-release limit.")
