from types import SimpleNamespace

import pytest

from seed.anomalies import cases
from seed.generate import generate
from shared.schema import LedgerKind

CONTRACT = {"auto_release_limit_usd": 25000, "escalation_confidence_threshold": 0.85, "progress_tolerance_points": 10}


def state_for(data: dict, request_id: str, contract: dict = CONTRACT):
    """The kernel's per-request slice, built the way pipeline.kernel_state builds it.

    Same attribute names as kernel.state.KernelState, which judgment reads.
    """
    req = next(r for r in data["pay_requests"] if r.id == request_id)

    def mine(rows):
        return [r for r in rows if r.sub_id == req.sub_id and r.job_id == req.job_id]

    payments = sorted((e for e in data["ledger"] if e.kind == LedgerKind.sub_payment and e.sub_id == req.sub_id),
                      key=lambda e: e.date)
    paid = {e.request_id for e in payments}
    vendor = next(v for v in data["vendors"] if v.id == req.sub_id)
    vendor = vendor.model_copy(update={"first_paid": payments[0].date if payments else None})
    state = SimpleNamespace(
        job=next(j for j in data["jobs"] if j.id == req.job_id),
        subcontract=next(c for c in data["subcontracts"] if c.sub_id == req.sub_id and c.job_id == req.job_id),
        change_orders=mine(data["change_orders"]),
        lower_tier_notices=mine(data["lower_tier_notices"]),
        coi=next((c for c in data["cois"] if c.sub_id == req.sub_id), None),
        vendor=vendor,
        prior_requests=[r for r in mine(data["pay_requests"]) if r.id in paid],
        all_requests=[r for r in mine(data["pay_requests"]) if r.id != req.id],
        releases=mine(data["releases"]),
        ledger=[e for e in data["ledger"] if e.job_id == req.job_id],
        emails=[e for e in data["emails"]
                if e.from_domain == vendor.email_domain or e.claims.new_account == req.bank_account],
        site_evidence=max((s for s in data["site_evidence"] if s.job_id == req.job_id and s.date <= req.period_end),
                          key=lambda s: s.date, default=None),
        known_bank_account=payments[-1].bank_account if payments else None,
        contract=contract,
    )
    return state, req


@pytest.fixture(scope="session")
def seed():
    return generate()


@pytest.fixture(scope="session")
def anomaly_cases():
    return list(cases())
