"""Rule 04: a signed conditional progress waiver must be on file for this sub, this job,
through this period's date, for at least the amount being paid. No release, not in the
request -- the sub doesn't get paid. A waiver only gives up lien rights up to its amount,
so one for less than the payment leaves the sub able to lien the job for the rest.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, ReleaseKind, RuleResult

RULE = "04_conditional_release_present"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    signed = [r for r in state.releases
              if r.sub_id == request.sub_id and r.job_id == request.job_id
              and r.kind == ReleaseKind.conditional_progress
              and r.through_date == request.period_end
              and r.signed]
    if not signed:
        return fail(RULE, {"sub_id": request.sub_id, "job_id": request.job_id,
                            "period_end": str(request.period_end)},
                     "No signed conditional release on file for this period.")
    due = request.g702.current_payment_due
    match = max(signed, key=lambda r: r.amount)
    if match.amount < due:
        return fail(RULE, {"release_id": match.id, "release_amount": str(match.amount), "payment_due": str(due)},
                     f"The lien release on file covers ${match.amount:,.2f}, less than the ${due:,.2f} being paid.")
    return ok(RULE, {"release_id": match.id, "through_date": str(match.through_date), "amount": str(match.amount)},
               "Signed conditional release on file through this period, covering the full payment.")
