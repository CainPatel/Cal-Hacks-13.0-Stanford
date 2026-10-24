"""Rule 12: no other request for this sub/job may share this period, or carry the exact
same line totals under a different application number -- a resubmission dressed up as a
new request.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "12_not_a_duplicate"


def _line_signature(request: PayRequest) -> tuple:
    return tuple(sorted((line.item_no, str(line.total_to_date)) for line in request.lines))


def check(state: KernelState, request: PayRequest) -> RuleResult:
    signature = _line_signature(request)
    duplicates = []
    for other in state.all_requests:
        if other.id == request.id:
            continue
        if other.number == request.number:
            if other.period_end == request.period_end:  # the same application submitted again (e.g. re-scanned)
                duplicates.append({"request_id": other.id, "reason": "same application number and period"})
            continue
        if other.period_end == request.period_end:
            duplicates.append({"request_id": other.id, "reason": "same period, different application number"})
        elif _line_signature(other) == signature:
            duplicates.append({"request_id": other.id, "reason": "identical line totals, different application number"})

    if duplicates:
        return fail(RULE, {"duplicates": duplicates},
                     f"This request looks like a resubmission of {len(duplicates)} earlier request(s).")
    return ok(RULE, {"requests_checked": len(state.all_requests)},
               "No earlier request shares this period or these exact line totals.")
