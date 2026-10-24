"""The view of contract state a rule needs. Not a new fact type — just the slice of
shared/schema.py models relevant to one pay request, assembled by the runner (or a
fixture) so every rule gets the same shape. Owner: A.
"""
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from shared.schema import (
    COI, ChangeOrder, Email, Job, LedgerEntry, LowerTierNotice, PayRequest,
    Proposal, Release, SiteEvidence, Subcontract, Vendor,
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
    # judgment/judge.py's output for this request, if judgment has run. None means
    # judgment hasn't been wired in yet (pipeline.py) or couldn't produce an answer --
    # rule 15 treats either case as "nothing to check," not as a failure.
    proposal: Proposal | None = None

    def prior_line(self, item_no: str, before_number: int) -> dict | None:
        """The most recent request's line for item_no among applications numbered before
        before_number, or None if there isn't one. The number filter matters once
        prior_requests holds full history rather than just what's already been paid --
        without it, deciding an old request could match against a *later* one.
        """
        for req in sorted((r for r in self.prior_requests if r.number < before_number),
                           key=lambda r: r.number, reverse=True):
            for line in req.lines:
                if line.item_no == item_no:
                    return line
        return None

    def requests_before(self, number: int) -> list[PayRequest]:
        """Every prior_requests entry numbered before `number` -- the same filter prior_line
        uses, for callers (rule 01) that need the most recent prior request as a whole.
        """
        return [r for r in self.prior_requests if r.number < number]

    def sov_scheduled_value(self, item_no: str) -> Decimal | None:
        """The contracted scheduled value for item_no, or None if the line isn't in the
        subcontract at all. The sub's own G703 also carries a scheduled_value, but that's
        self-reported and must never be the ceiling -- only the contract is authoritative.
        """
        return next((l.scheduled_value for l in self.subcontract.sov if l.item_no == item_no), None)

    def approved_co_total(self, item_no: str) -> Decimal:
        return sum(
            (co.amount for co in self.change_orders if co.item_no == item_no and co.approved),
            Decimal("0"),
        )
