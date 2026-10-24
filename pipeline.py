"""The one path every pay request takes to a decision. Owner: B.

Replay and the fuzzer both call `decide(state, request_id)`, so the scorecard measures the
real path. `state` is the whole world as known at decision time (events.log.state(), or
seed.generate()); this module cuts out the slice one request needs (kernel.state.KernelState)
and hands it to A's pure runner. `judge_and_decide` runs C's judgment first and gives the
kernel its Proposal (rule 15); `decide` alone skips the model, so the fuzzer stays offline.

Until kernel/ exists, a stub that releases everything stands in, so any number it produces
is obviously wrong rather than plausibly right.
"""
from functools import cache

from shared.schema import Action, Decision, LedgerKind

try:
    from kernel.contract import load_contract
    from kernel.runner import decide as kernel_decide
    from kernel.state import KernelState
    KERNEL = "kernel.runner"
except ImportError:
    kernel_decide = None
    KERNEL = "STUB (releases everything; kernel/ not merged yet)"


def kernel_state(state: dict[str, list], request_id: str, contract: dict):
    """The slice of the world one request is judged on. Derived facts come from the ledger,
    never from what a sub or a seed record claims."""
    req = next(r for r in state["pay_requests"] if r.id == request_id)

    def mine(rows):
        return [r for r in rows if r.sub_id == req.sub_id and r.job_id == req.job_id]

    payments = sorted((e for e in state["ledger"] if e.kind == LedgerKind.sub_payment and e.sub_id == req.sub_id),
                      key=lambda e: e.date)
    paid = {e.request_id for e in payments}
    vendor = next(v for v in state["vendors"] if v.id == req.sub_id)
    vendor = vendor.model_copy(update={"first_paid": payments[0].date if payments else None})
    return KernelState(
        job=next(j for j in state["jobs"] if j.id == req.job_id),
        subcontract=next(c for c in state["subcontracts"] if c.sub_id == req.sub_id and c.job_id == req.job_id),
        vendor=vendor,
        change_orders=mine(state["change_orders"]),
        prior_requests=[r for r in mine(state["pay_requests"]) if r.id in paid],  # only what we certified
        all_requests=[r for r in mine(state["pay_requests"]) if r.id != req.id],
        releases=mine(state["releases"]),
        lower_tier_notices=mine(state["lower_tier_notices"]),
        coi=next((c for c in state["cois"] if c.sub_id == req.sub_id), None),
        ledger=[e for e in state["ledger"] if e.job_id == req.job_id],
        # the sub's own mail, plus any email asking us to pay the account on this request
        emails=[e for e in state["emails"]
                if e.from_domain == vendor.email_domain or e.claims.new_account == req.bank_account],
        site_evidence=max((s for s in state["site_evidence"] if s.job_id == req.job_id and s.date <= req.period_end),
                          key=lambda s: s.date, default=None),
        known_bank_account=payments[-1].bank_account if payments else None,
        contract=contract,
    ), req


def decide(state: dict[str, list], request_id: str, contract: dict | None = None, judge=None) -> Decision:
    """The kernel's decision. Pass `judge` (e.g. judgment.judge) to put the model in the loop."""
    if judge is not None:
        return judge_and_decide(state, request_id, contract, judge)[0]
    if kernel_decide is None:
        return Decision(request_id=request_id, action=Action.release, results=[])
    ks, req = kernel_state(state, request_id, contract if contract is not None else default_contract())
    return kernel_decide(ks, req)


def judge_and_decide(state: dict[str, list], request_id: str, contract: dict | None = None, judge=None):
    """(Decision, JudgmentResult): judgment answers the five questions on the same slice the
    kernel sees, then the kernel decides with that Proposal in hand. Judgment never raises;
    if no model answers, its Proposal has zero confidence and rule 15 escalates."""
    if kernel_decide is None:
        raise RuntimeError("judge_and_decide needs kernel/")
    if judge is None:
        from judgment import judge
    ks, req = kernel_state(state, request_id, contract if contract is not None else default_contract())
    result = judge(ks, req)
    return kernel_decide(ks.model_copy(update={"proposal": result.proposal}), req), result


def judgment_event(result) -> dict:
    """What the log keeps of a judgment: the state sent to the model and every answer (calibration needs both)."""
    return {
        "source": result.source,
        "proposal": result.proposal.model_dump(mode="json"),
        "answers": {q: {"value": a.value, "confidence": a.confidence, "probabilities": a.probabilities}
                    for q, a in result.answers.items()},
        "facts": result.facts,
        "errors": result.errors,
    }


if kernel_decide is not None:
    default_contract = cache(load_contract)  # read once per process; pass `contract` for what-ifs
