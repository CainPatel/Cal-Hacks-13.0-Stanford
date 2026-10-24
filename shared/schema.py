"""Every model the repo shares. Add a field here first, with a fixture. Owner: A.

Money is Decimal (never float). Percentages are 0-100. Models are frozen; use
model_copy(update=...) to derive a changed one (the fuzzer does this).
"""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- contract state ---------------------------------------------------------

class Job(Model):
    id: str
    name: str
    owner: str
    lender: str
    architect: str
    contract_sum: Decimal
    retainage_rate: Decimal  # 0.10 = 10%
    pay_when_paid: bool


class SOVLine(Model):
    item_no: str
    description: str
    scheduled_value: Decimal


class Subcontract(Model):
    id: str
    sub_id: str
    job_id: str
    sov: list[SOVLine]
    retainage_rate: Decimal


class ChangeOrder(Model):
    id: str
    job_id: str
    sub_id: str
    item_no: str
    amount: Decimal
    approved: bool
    date: date


class Vendor(Model):
    id: str
    legal_name: str
    ein: str
    email_domain: str
    bank_account: str
    first_paid: date | None
    crs_status: str  # verified | mismatch | flagged | unknown


# --- what a sub submits -----------------------------------------------------

class LineItem(Model):
    """One G703 row, as the sub claims it."""
    item_no: str
    description: str
    scheduled_value: Decimal
    previous_applications: Decimal
    this_period: Decimal
    stored_materials: Decimal
    total_to_date: Decimal  # previous + this_period + stored
    pct_complete: Decimal
    balance_to_finish: Decimal
    retainage: Decimal


class G702(Model):
    original_contract_sum: Decimal
    net_change_orders: Decimal
    contract_sum_to_date: Decimal
    total_completed_stored: Decimal
    retainage: Decimal
    total_earned_less_retainage: Decimal
    less_previous_certificates: Decimal
    current_payment_due: Decimal


class PayRequest(Model):
    id: str
    number: int  # the sub's application number
    sub_id: str
    job_id: str
    period_end: date
    lines: list[LineItem]
    g702: G702
    bank_account: str
    submitted_at: datetime
    source_doc: str


class ReleaseKind(str, Enum):
    conditional_progress = "conditional_progress"
    unconditional_progress = "unconditional_progress"
    conditional_final = "conditional_final"
    unconditional_final = "unconditional_final"


class Release(Model):
    id: str
    sub_id: str
    job_id: str
    kind: ReleaseKind
    through_date: date
    amount: Decimal
    signed: bool
    received_at: date


class LowerTierNotice(Model):
    id: str
    supplier: str
    sub_id: str
    job_id: str
    notice_date: date
    released: bool


class COI(Model):
    id: str
    sub_id: str
    gl_expiry: date
    wc_expiry: date
    additional_insured: bool


class EmailClaims(Model):
    bank_change: bool = False
    new_account: str | None = None
    urgency: bool = False


class Email(Model):
    id: str
    from_addr: str
    from_domain: str
    sent_at: datetime
    subject: str
    body: str
    claims: EmailClaims


class SiteEvidence(Model):
    id: str
    job_id: str
    date: date
    frames: list[str]
    estimates: dict[str, Decimal]  # item_no -> pct complete seen on video


class LedgerKind(str, Enum):
    owner_draw = "owner_draw"
    sub_payment = "sub_payment"


class LedgerEntry(Model):
    """Bank ledger (Nessie). Draws in, sub payments out."""
    id: str
    job_id: str
    kind: LedgerKind
    amount: Decimal
    date: date
    period_end: date
    sub_id: str | None = None
    request_id: str | None = None
    bank_account: str | None = None


# --- decisions --------------------------------------------------------------

class Action(str, Enum):
    release = "release"
    hold = "hold"
    escalate = "escalate"


class Proposal(Model):
    action: Action
    confidences: dict[str, float]
    reasons: list[str]


class RuleResult(Model):
    rule: str
    passed: bool
    evidence: dict
    message: str  # plain English, user-facing


class Decision(Model):
    request_id: str
    action: Action
    results: list[RuleResult]
    next_action: str | None = None
    signature: str | None = None
    executed_at: datetime | None = None


class Label(Model):
    """Ground truth for a seeded or fuzzed request. Never passed to the kernel."""
    request_id: str
    anomaly: str | None  # None = clean
    expected_action: Action
    rules: list[str]  # rules that should fire
