"""Rule 01: this request's "previous applications" must equal last month's "total to
date" in our own ledger, per line. Never trust the sub's number; we recompute from the
prior request we actually certified.
"""
from decimal import Decimal

from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "01_cumulative_continuity"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    mismatches = []
    for line in request.lines:
        prior = state.prior_line(line.item_no)
        expected = prior.total_to_date if prior else Decimal("0")
        if line.previous_applications != expected:
            mismatches.append({
                "item_no": line.item_no,
                "claimed_previous": str(line.previous_applications),
                "ledger_previous": str(expected),
            })

    if mismatches:
        return fail(RULE, {"mismatches": mismatches},
                     f"{len(mismatches)} line(s) don't start from what we actually paid last month.")
    return ok(RULE, {"lines_checked": len(request.lines)},
               "Every line's starting balance matches our ledger.")
