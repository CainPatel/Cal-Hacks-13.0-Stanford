"""Stand-in for shared/schema.py until it lands.

Field names follow the core-schema summary in CLAUDE.md. Types and optionality
are guesses and must be reconciled with the real shared/schema.py; perception
imports through perception/schema.py, which prefers the real module.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class LineItem(BaseModel):
    item_no: str
    description: str
    scheduled_value: Decimal
    previous_applications: Decimal
    this_period: Decimal
    stored_materials: Decimal
    total_to_date: Decimal
    pct_complete: Decimal  # percentage points, 0-100, as printed
    balance_to_finish: Decimal
    retainage: Decimal | None  # None when the form has no retainage column


class G702Totals(BaseModel):
    original_contract_sum: Decimal
    net_change_orders: Decimal
    contract_sum_to_date: Decimal
    total_completed_and_stored: Decimal
    retainage: Decimal
    total_earned_less_retainage: Decimal
    less_previous_certificates: Decimal
    current_payment_due: Decimal
    balance_to_finish: Decimal


class PayRequest(BaseModel):
    id: str
    sub_id: str
    job_id: str
    period_end: date
    lines: list[LineItem]
    g702: G702Totals | None
    bank_account: str | None
    submitted_at: datetime
    source_doc: str


class SiteEvidence(BaseModel):
    job_id: str
    date: date
    frames: list[str]
    estimates: dict[str, float]  # item_no -> estimated percent complete, 0-100
