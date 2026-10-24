"""Rule 10: the request's payment destination must match the account on file. The
vendor's bank_account is the verified account of record -- a human updates it only after
confirming a change out-of-band (the number on file, not the number in a new email).
A mismatch here means nobody has verified the new account yet; that's the $1.5M wire.

Separately, kernel/runner.py always escalates on any change from the last *paid* account
(state.known_bank_account) even once it's verified here -- a verified change still gets
a physical tap before money moves under the new account for the first time.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "10_bank_details_unchanged"


def check(state: KernelState, request: PayRequest) -> RuleResult:
    on_file = state.vendor.bank_account
    if request.bank_account != on_file:
        return fail(RULE, {"on_file": on_file, "requested": request.bank_account, "verification_status": "unverified"},
                     "This request's bank account doesn't match the verified account on file.")
    return ok(RULE, {"bank_account": on_file, "verification_status": "verified"},
               "Bank account matches the verified account on file.")
