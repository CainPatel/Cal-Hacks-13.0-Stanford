"""Rule 07: every lower-tier supplier that filed a notice to owner against this sub/job
must have released. This is the $70K near-miss from the interview: a sub provided their
own release, but their concrete supplier had filed a notice and never released, and the
office manager missed it.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "07_lower_tier_released"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    unresolved = [
        n for n in state.lower_tier_notices
        if n.sub_id == request.sub_id and n.job_id == request.job_id and not n.released
    ]
    if unresolved:
        return fail(RULE, {"unresolved": [
            {"supplier": n.supplier, "notice_date": str(n.notice_date), "released": n.released}
            for n in unresolved
        ]}, f"{len(unresolved)} supplier(s) filed a notice to owner and haven't released.")
    return ok(RULE, {"notices_checked": len([
        n for n in state.lower_tier_notices if n.sub_id == request.sub_id and n.job_id == request.job_id
    ])}, "Every lower-tier supplier with a notice on file has released.")
