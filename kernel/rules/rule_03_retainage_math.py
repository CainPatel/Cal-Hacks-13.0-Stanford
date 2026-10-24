"""Rule 03: retainage is the contract rate times cumulative total-to-date, per line and
on the G702 summary, and it isn't released early -- only a final release lets retainage
dollars go out.
"""
from decimal import ROUND_HALF_UP, Decimal

from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, ReleaseKind, RuleResult

RULE = "03_retainage_math"
CENT = Decimal("0.01")


def check(state: KernelState, request: PayRequest) -> RuleResult:
    rate = state.subcontract.retainage_rate
    mismatches = []
    for line in request.lines:
        expected = (line.total_to_date * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        if line.retainage != expected:
            mismatches.append({
                "item_no": line.item_no,
                "claimed_retainage": str(line.retainage),
                "expected_retainage": str(expected),
                "contract_rate": str(rate),
            })

    g702_expected = sum((line.retainage for line in request.lines), Decimal("0"))
    if request.g702.retainage != g702_expected:
        mismatches.append({
            "item_no": "G702_TOTAL",
            "claimed_retainage": str(request.g702.retainage),
            "expected_retainage": str(g702_expected),
            "contract_rate": str(rate),
        })

    is_final = any(
        r.kind in (ReleaseKind.conditional_final, ReleaseKind.unconditional_final)
        and r.through_date >= request.period_end
        for r in state.releases
    )
    if not is_final and request.g702.retainage <= 0 and request.g702.total_completed_stored > 0:
        mismatches.append({
            "item_no": "G702_TOTAL",
            "claimed_retainage": str(request.g702.retainage),
            "expected_retainage": "> 0 (not a final payment)",
            "contract_rate": str(rate),
        })

    if mismatches:
        return fail(RULE, {"mismatches": mismatches},
                     f"Retainage doesn't match the contract rate on {len(mismatches)} line(s).")
    return ok(RULE, {"rate": str(rate), "lines_checked": len(request.lines)},
               f"Retainage matches the contract's {rate * 100}% rate.")
