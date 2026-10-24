"""Rule 13: claimed percent-complete can't outrun the super's site video by more than the
contract's tolerance. This is the deterministic half of "progress plausible" -- the
kernel checks the numbers it can verify on its own; judgment/judge.py's Jev question adds
a qualitative read on top, surfaced through rule 15's confidence floor, not duplicated here.

Understated claims aren't flagged: a sub billing less than the video shows isn't the
fraud this rule exists to catch.
"""
from decimal import Decimal

from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "13_progress_plausible"
DEFAULT_TOLERANCE = Decimal("10")


def check(state: KernelState, request: PayRequest) -> RuleResult:
    if state.site_evidence is None:
        return ok(RULE, {"site_evidence": None}, "No site video on file for this period yet.")

    tolerance = Decimal(str(state.contract.get("progress_tolerance_points", DEFAULT_TOLERANCE)))
    overstatements = []
    for line in request.lines:
        estimate = state.site_evidence.estimates.get(line.item_no)
        if estimate is None:
            continue
        gap = line.pct_complete - estimate
        if gap > tolerance:
            overstatements.append({
                "item_no": line.item_no, "claimed_pct": str(line.pct_complete),
                "video_estimate_pct": str(estimate), "gap": str(gap), "tolerance": str(tolerance),
            })

    if overstatements:
        return fail(RULE, {"overstatements": overstatements},
                     f"{len(overstatements)} line(s) claim more progress than the site video shows.")
    return ok(RULE, {"tolerance": str(tolerance), "lines_checked": len(request.lines)},
               "Claimed progress is within tolerance of the site video.")
