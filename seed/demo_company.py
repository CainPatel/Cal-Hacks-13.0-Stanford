"""A subcontractor whose first pay application arrives by email during the demo.

Everything the GC already has on file before that email: the vendor, the subcontract, insurance,
a signed conditional release, the owner's draw and the super's site video for the period. Not the
pay application itself (G703 and G702): that comes in through the inbox. The contract lines are
read from the very pay application the sub will send, so the contract and the request agree.

seed/demo_company_files/ holds it: company.json and the two PDFs to email. The PDFs are a real
spreadsheet-exported pay application with every name and address replaced by fictional ones,
retainage set to the demo world's 10% on every line, and the period moved to October 2026.

company.json:

    {"sub_id": "sub_16", "legal_name": "...", "email_domain": "...", "ein": "12-3456789",
     "job": {"id": "job_4", "name": "...", "owner": "...", "lender": "...", "architect": "..."},
     "g703": "g703.pdf", "g702": "g702.pdf",
     "site_video": {"date": "2026-10-22", "estimates": {"145": 45}}}

The sub's email domain must be exactly the one the demo email is sent from: attachments are filed
under a sub only on an exact domain match (perception/attachments.py).
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from shared.schema import (
    COI, Job, LedgerEntry, LedgerKind, Release, ReleaseKind, SiteEvidence, SOVLine, Subcontract, Vendor,
)

FOLDER = Path(__file__).parent / "demo_company_files"
RETAINAGE = Decimal("0.10")


def facts(folder: Path = FOLDER) -> dict[str, list] | None:
    """The sub's records, by state table, or None when the folder has no company.json."""
    config = folder / "company.json"
    if not config.exists():
        return None
    from perception import extract_pay_request

    c = json.loads(config.read_text())
    job_id, sub_id = c["job"]["id"], c["sub_id"]
    pay_app = extract_pay_request(folder / c["g703"], sub_id=sub_id, job_id=job_id, submitted_at=datetime.now(),
                                  g702_pdf=folder / c["g702"] if c.get("g702") else None,
                                  allow_llm_fallback=False).pay_request
    period_end, due = pay_app.period_end, pay_app.g702.current_payment_due
    month_end = (period_end.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    video = c.get("site_video", {})
    return {
        "jobs": [Job(id=job_id, name=c["job"]["name"], owner=c["job"]["owner"], lender=c["job"]["lender"],
                     architect=c["job"]["architect"], contract_sum=pay_app.g702.contract_sum_to_date,
                     retainage_rate=RETAINAGE, pay_when_paid=True)],
        # The account on file is the one the pay application asks to be paid at, so rule 10 passes.
        "vendors": [Vendor(id=sub_id, legal_name=c["legal_name"], ein=c["ein"], email_domain=c["email_domain"],
                           bank_account=c.get("bank_account", pay_app.bank_account), first_paid=None,
                           crs_status="verified")],
        "subcontracts": [Subcontract(id=f"sc_{sub_id}", sub_id=sub_id, job_id=job_id, retainage_rate=RETAINAGE,
                                     sov=[SOVLine(item_no=l.item_no, description=l.description,
                                                  scheduled_value=l.scheduled_value) for l in pay_app.lines])],
        "cois": [COI(id=f"coi_{sub_id}", sub_id=sub_id, gl_expiry=date(2027, 9, 30), wc_expiry=date(2027, 8, 31),
                     additional_insured=True)],
        "releases": [Release(id=f"rel_c_{sub_id}_{pay_app.number}", sub_id=sub_id, job_id=job_id,
                             kind=ReleaseKind.conditional_progress, through_date=period_end, amount=due,
                             signed=True, received_at=period_end - timedelta(days=1))],
        # the owner's draw for the period clears on the 5th of the next month, like the seed's
        "ledger": [LedgerEntry(id=f"draw_{job_id}_{period_end:%Y%m}", job_id=job_id, kind=LedgerKind.owner_draw,
                               amount=(due * Decimal("1.15")).quantize(Decimal("0.01")),
                               date=(month_end + timedelta(days=1)).replace(day=5), period_end=period_end)],
        # seeded, so the dashboard labels it as sample numbers (its id isn't a read video's)
        "site_evidence": [SiteEvidence(id=f"site_{job_id}_{period_end:%Y%m}", job_id=job_id,
                                       date=date.fromisoformat(video.get("date", str(period_end - timedelta(days=3)))),
                                       frames=[], estimates={k: Decimal(str(v))
                                                             for k, v in video.get("estimates", {}).items()})],
    }


def add(data: dict[str, list], folder: Path = FOLDER) -> dict[str, list]:
    """The seed with the demo company's records added."""
    extra = facts(folder)
    for table, rows in (extra or {}).items():
        data[table] = data.get(table, []) + rows
    return data
