"""Rule 08: the owner's draw for this period must have cleared before we pay subs.
Pay-when-paid is near-absolute per the interview; the kernel only sees ledger facts
once they're known, so an owner_draw entry existing in state means it already happened.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import LedgerKind, PayRequest, RuleResult

RULE = "08_owner_draw_funded"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    if not state.job.pay_when_paid:
        return ok(RULE, {"pay_when_paid": False}, "This job doesn't require the owner's draw to clear first.")

    draw = next(
        (e for e in state.ledger
         if e.kind == LedgerKind.owner_draw and e.job_id == request.job_id and e.period_end == request.period_end),
        None,
    )
    if draw is None:
        return fail(RULE, {"job_id": request.job_id, "period_end": str(request.period_end)},
                     "The owner's draw for this period hasn't cleared yet.")
    return ok(RULE, {"draw_id": draw.id, "amount": str(draw.amount), "date": str(draw.date)},
               "The owner's draw for this period has cleared.")
