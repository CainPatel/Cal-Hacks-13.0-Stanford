"""Rule 05: if we already paid the sub for the immediately prior period, their
unconditional waiver for that period must be on file by now. A conditional waiver comes
with the request; the unconditional one is supposed to follow about a month later, once
payment clears -- this check makes sure it actually did.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import LedgerKind, PayRequest, ReleaseKind, RuleResult

RULE = "05_prior_unconditional_received"


def _most_recent_prior(state: KernelState, request: PayRequest):
    priors = [r for r in state.prior_requests if r.number < request.number]
    return max(priors, key=lambda r: r.number, default=None)


def check(state: KernelState, request: PayRequest) -> RuleResult:
    prior = _most_recent_prior(state, request)
    if prior is None:
        return ok(RULE, {"prior_request": None}, "This is the first request; nothing prior to verify.")

    payment = next(
        (e for e in state.ledger
         if e.kind == LedgerKind.sub_payment and e.request_id == prior.id),
        None,
    )
    if payment is None:
        return ok(RULE, {"prior_request": prior.id, "paid": False},
                   "Last period hasn't been paid yet; no unconditional waiver is owed.")

    waiver = next(
        (r for r in state.releases
         if r.sub_id == request.sub_id and r.job_id == request.job_id
         and r.kind in (ReleaseKind.unconditional_progress, ReleaseKind.unconditional_final)
         and r.through_date == prior.period_end
         and r.signed),
        None,
    )
    if waiver is None:
        return fail(RULE, {"prior_payment_id": payment.id, "prior_request": prior.id},
                     "Last period's payment cleared but the unconditional waiver never came in.")
    return ok(RULE, {"prior_payment_id": payment.id, "waiver_id": waiver.id},
               "Last period's unconditional waiver is on file.")
