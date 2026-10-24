"""Rule 04: a signed conditional progress waiver must be on file for this sub, this job,
through this period's date. No release, not in the request -- the sub doesn't get paid.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, ReleaseKind, RuleResult

RULE = "04_conditional_release_present"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    match = next(
        (r for r in state.releases
         if r.sub_id == request.sub_id and r.job_id == request.job_id
         and r.kind == ReleaseKind.conditional_progress
         and r.through_date == request.period_end
         and r.signed),
        None,
    )
    if match is None:
        return fail(RULE, {"sub_id": request.sub_id, "job_id": request.job_id,
                            "period_end": str(request.period_end)},
                     "No signed conditional release on file for this period.")
    return ok(RULE, {"release_id": match.id, "through_date": str(match.through_date)},
               "Signed conditional release on file through this period.")
