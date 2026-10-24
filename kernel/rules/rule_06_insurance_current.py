"""Rule 06: GL and workers' comp must still be in force on the date this payment is
expected to go out, and the GC must be listed as additional insured. Expired COI = held;
the office manager tracks this by hand today.
"""
from datetime import date, timedelta

from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "06_insurance_current"


def expected_payment_date(period_end: date) -> date:
    """Subs are paid by the 10th of the month after the period ends (CLAUDE.md: "The GC
    pays subs by the 10th"). Insurance has to still be good on that date, not just today.
    """
    next_month = period_end + timedelta(days=1)
    return next_month.replace(day=10)


def check(state: KernelState, request: PayRequest) -> RuleResult:
    if state.coi is None:
        return fail(RULE, {"sub_id": request.sub_id}, "No certificate of insurance on file.")

    pay_date = expected_payment_date(request.period_end)
    problems = []
    if state.coi.gl_expiry <= pay_date:
        problems.append(f"general liability expires {state.coi.gl_expiry}, before the {pay_date} payment date")
    if state.coi.wc_expiry <= pay_date:
        problems.append(f"workers' comp expires {state.coi.wc_expiry}, before the {pay_date} payment date")
    if not state.coi.additional_insured:
        problems.append("the GC isn't listed as additional insured")

    if problems:
        return fail(RULE, {"coi_id": state.coi.id, "gl_expiry": str(state.coi.gl_expiry),
                            "wc_expiry": str(state.coi.wc_expiry), "payment_date": str(pay_date),
                            "problems": problems},
                     "; ".join(problems).capitalize() + ".")
    return ok(RULE, {"coi_id": state.coi.id, "gl_expiry": str(state.coi.gl_expiry),
                      "wc_expiry": str(state.coi.wc_expiry)},
               "Insurance is current through the expected payment date.")
