"""Rule 01: this request's "previous applications" must equal last month's "total to
date" in our own ledger, per line, and the G702's payment due must be what cumulative
billing says: (total to date - retainage) - what we already certified. Never trust the
sub's numbers; we recompute from the prior request we actually certified. The payment due
is the amount the executor pays, so it can't be taken on the sub's word.
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

    prior = max(state.prior_requests, key=lambda r: r.number, default=None)
    certified = prior.g702.total_earned_less_retainage if prior else Decimal("0")
    due = sum((l.total_to_date - l.retainage for l in request.lines), Decimal("0")) - certified
    g = request.g702
    if g.less_previous_certificates != certified or g.current_payment_due != due:
        return fail(RULE, {"mismatches": mismatches, "claimed_payment_due": str(g.current_payment_due),
                           "computed_payment_due": str(due),
                           "claimed_previous_certificates": str(g.less_previous_certificates),
                           "ledger_previous_certificates": str(certified)},
                     f"The payment due (${g.current_payment_due:,.2f}) doesn't add up; "
                     f"by our ledger it should be ${due:,.2f}.")
    if mismatches:
        return fail(RULE, {"mismatches": mismatches},
                     f"{len(mismatches)} line(s) don't start from what we actually paid last month.")
    return ok(RULE, {"lines_checked": len(request.lines)},
               "Every line's starting balance matches our ledger.")
