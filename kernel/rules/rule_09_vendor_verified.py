"""Rule 09: the vendor must have passed CRS identity verification. Anything other than
"verified" (mismatch, flagged, unknown) holds -- this is the new-sub-fraud check.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "09_vendor_verified"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    status = state.vendor.crs_status
    if status != "verified":
        return fail(RULE, {"vendor_id": state.vendor.id, "crs_status": status},
                     f"CRS identity check for this vendor came back {status!r}, not verified.")
    return ok(RULE, {"vendor_id": state.vendor.id, "crs_status": status},
               "Vendor identity is verified with CRS.")
