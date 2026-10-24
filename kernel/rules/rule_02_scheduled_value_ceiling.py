"""Rule 02: total-to-date per line can't exceed the scheduled value plus any approved
change orders for that line. Over-billing past contract is the GC's recurring dread.

The ceiling comes from the subcontract on file, never from the line's own
scheduled_value -- that field is self-reported by the sub on their G703, and a sub
inflating it would otherwise raise their own ceiling and bypass this check entirely.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "02_scheduled_value_ceiling"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    overages = []
    for line in request.lines:
        contract_value = state.sov_scheduled_value(line.item_no)
        if contract_value is None:
            overages.append({
                "item_no": line.item_no,
                "total_to_date": str(line.total_to_date),
                "scheduled_value": "not in subcontract",
                "approved_cos": "0",
                "ceiling": "0",
                "overage": str(line.total_to_date),
            })
            continue
        co_total = state.approved_co_total(line.item_no)
        ceiling = contract_value + co_total
        if line.total_to_date > ceiling:
            overages.append({
                "item_no": line.item_no,
                "total_to_date": str(line.total_to_date),
                "scheduled_value": str(contract_value),
                "approved_cos": str(co_total),
                "ceiling": str(ceiling),
                "overage": str(line.total_to_date - ceiling),
            })

    if overages:
        return fail(RULE, {"overages": overages},
                     f"{len(overages)} line(s) are billed past contract plus approved change orders.")
    return ok(RULE, {"lines_checked": len(request.lines)},
               "No line is billed past its scheduled value plus approved change orders.")
