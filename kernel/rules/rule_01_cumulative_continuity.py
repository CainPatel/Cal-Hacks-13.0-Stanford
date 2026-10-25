"""Rule 01: this request's "previous applications" must equal last month's "total to
date" in our own ledger, per line, and the G702's payment due must be what cumulative
billing says: (total to date - retainage) - what we already certified. Never trust the
sub's numbers; we recompute from the prior request we actually certified. The payment due
is the amount the executor pays, so it can't be taken on the sub's word.

"What we already certified" is recomputed from the prior request's lines, not read off its
G702 line 6: nothing checks line 6, so a sub who understated it one month would be paid
that month's work again the next. Each line item may appear once (a repeated item would
count its previous billing twice and pass every per-line check), and the payment due
can't be negative. proofs/ checks with Z3 that, together with rules 02, 03 and 13, this
keeps total payments within the contract and the site video.
"""
from collections import Counter
from decimal import Decimal

from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "01_cumulative_continuity"


def earned_less_retainage(request: PayRequest) -> Decimal:
    return sum((l.total_to_date - l.retainage for l in request.lines), Decimal("0"))


def check(state: KernelState, request: PayRequest) -> RuleResult:
    repeated = sorted(item for item, n in Counter(l.item_no for l in request.lines).items() if n > 1)
    if repeated:
        return fail(RULE, {"repeated_items": repeated},
                    f"Line item {', '.join(repeated)} appears more than once on the continuation sheet.")

    mismatches = []
    for line in request.lines:
        prior = state.prior_line(line.item_no, request.number)
        expected = prior.total_to_date if prior else Decimal("0")
        if line.previous_applications != expected:
            mismatches.append({
                "item_no": line.item_no,
                "claimed_previous": str(line.previous_applications),
                "ledger_previous": str(expected),
            })

    prior = max(state.requests_before(request.number), key=lambda r: r.number, default=None)
    certified = earned_less_retainage(prior) if prior else Decimal("0")
    due = earned_less_retainage(request) - certified
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
    if due < 0:
        return fail(RULE, {"computed_payment_due": str(due)},
                    f"This request bills ${-due:,.2f} less than we already paid; a credit has to be handled by hand.")
    return ok(RULE, {"lines_checked": len(request.lines)},
               "Every line's starting balance matches our ledger.")
