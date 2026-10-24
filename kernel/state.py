"""The view of contract state a rule needs. Not a new fact type — just the slice of
shared/schema.py models relevant to one pay request, assembled by the runner (or a
fixture) so every rule gets the same shape. Owner: A.
"""
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from shared.schema import (
    COI, ChangeOrder, Email, Job, LedgerEntry, LowerTierNotice, PayRequest,
    Release, SiteEvidence, Subcontract, Vendor,
)


class KernelState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job: Job
    subcontract: Subcontract
    vendor: Vendor
    change_orders: list[ChangeOrder] = []
    # every earlier PayRequest for this sub+job, any order; rules sort by number/period_end
    prior_requests: list[PayRequest] = []
    # every request ever seen for this sub+job (prior_requests plus any other submission,
    # including ones under a different number) -- for duplicate detection
    all_requests: list[PayRequest] = []
    releases: list[Release] = []
    lower_tier_notices: list[LowerTierNotice] = []
    coi: COI | None = None
    ledger: list[LedgerEntry] = []
    emails: list[Email] = []
    site_evidence: SiteEvidence | None = None
    # bank account on file for the last successful payment to this sub, if any
    known_bank_account: str | None = None
    # permissions contract, as loaded from config/contract.yaml
    contract: dict = {}

    def prior_line(self, item_no: str) -> dict | None:
        """The most recent prior request's line for item_no, or None if this is the first."""
        for req in sorted(self.prior_requests, key=lambda r: r.number, reverse=True):
            for line in req.lines:
                if line.item_no == item_no:
                    return line
        return None

    def approved_co_total(self, item_no: str) -> Decimal:
        return sum(
            (co.amount for co in self.change_orders if co.item_no == item_no and co.approved),
            Decimal("0"),
        )
