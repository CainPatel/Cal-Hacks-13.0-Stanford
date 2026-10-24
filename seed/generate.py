"""Clean seed data: one GC, 3 jobs, 15 subs, 6 months of pay requests. Owner: B.

Every request here is clean. Anomalies are planted on top of this in a later
step, each with a Label. Deterministic: same RNG seed -> same data.

    python -m seed.generate            # writes data/seed.db
"""
import json
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from shared.schema import (
    COI, G702, Job, LedgerEntry, LedgerKind, LineItem, LowerTierNotice, PayRequest,
    Release, ReleaseKind, SiteEvidence, SOVLine, Subcontract, Vendor,
)

CENT = Decimal("0.01")
RETAINAGE = Decimal("0.10")
PERIODS = [date(2026, m, 1) for m in range(5, 11)]  # May..Oct 2026; Oct is "this month"

# (job name, owner, [(vendor legal name, domain, CSI division, trade)])
JOBS = [
    ("Harbor Point Medical Office", "Harbor Point Health LLC", [
        ("Coastal Concrete Inc.", "coastalconcrete.com", "03", "concrete"),
        ("Bayline Steel Co.", "baylinesteel.com", "05", "steel"),
        ("Summit Drywall LLC", "summitdrywall.com", "09", "drywall"),
        ("Brightline Electric Inc.", "brightlineelectric.com", "26", "electrical"),
        ("Delta Plumbing Corp.", "deltaplumbingcorp.com", "22", "plumbing"),
    ]),
    ("Elm Street Retail Center", "Elm Street Partners LP", [
        ("Granite Ridge Concrete", "graniteridgeconcrete.com", "03", "concrete"),
        ("Ironworks West LLC", "ironworkswest.com", "05", "steel"),
        ("Redwood Framing Co.", "redwoodframing.com", "06", "framing"),
        ("Pacific Roofing Inc.", "pacificroofing.com", "07", "roofing"),
        ("Cascade Mechanical", "cascademech.com", "23", "hvac"),
    ]),
    ("Westgate Office Renovation", "Westgate Holdings Inc.", [
        ("Mission Masonry Inc.", "missionmasonry.com", "04", "masonry"),
        ("Keystone Drywall Co.", "keystonedrywall.com", "09", "drywall"),
        ("Volt Electric LLC", "voltelectricllc.com", "26", "electrical"),
        ("Clearwater Plumbing", "clearwaterplumbing.com", "22", "plumbing"),
        ("Airflow HVAC Inc.", "airflowhvac.com", "23", "hvac"),
    ]),
]

SCOPES = {
    "concrete": ["Footings and foundations", "Slab on grade", "Elevated decks", "Site concrete"],
    "steel": ["Structural steel", "Metal deck", "Misc. metals and stairs"],
    "framing": ["Wall framing", "Roof trusses", "Sheathing", "Blocking and backing"],
    "roofing": ["Roof membrane", "Flashing and sheet metal", "Roof insulation"],
    "masonry": ["CMU walls", "Brick veneer", "Stone trim"],
    "drywall": ["Metal stud framing", "Hang and tape", "Ceilings", "Finish level 5"],
    "electrical": ["Underground and rough-in", "Switchgear", "Branch wiring", "Lighting fixtures", "Fire alarm"],
    "plumbing": ["Underground plumbing", "Rough-in", "Fixtures", "Water heaters"],
    "hvac": ["Ductwork", "Rooftop units", "Controls", "Test and balance"],
}


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT)


def generate(rng_seed: int = 13) -> dict[str, list]:
    rng = random.Random(rng_seed)
    out = {k: [] for k in ["jobs", "vendors", "subcontracts", "change_orders", "pay_requests",
                           "releases", "lower_tier_notices", "cois", "ledger", "emails",
                           "site_evidence", "labels"]}
    vendors = {}

    for j, (job_name, owner, subs) in enumerate(JOBS, 1):
        job_id = f"job_{j}"
        contracts = []
        for name, domain, div, trade in subs:
            sub_id = f"sub_{len(vendors) + 1:02d}"
            scopes = SCOPES[trade][: rng.randint(3, len(SCOPES[trade]))]
            sov = [SOVLine(item_no=f"{div}-{k}0", description=d,
                           scheduled_value=money(rng.randrange(400, 4000) * 100))
                   for k, d in enumerate(scopes, 1)]
            contracts.append(Subcontract(id=f"sc_{sub_id}", sub_id=sub_id, job_id=job_id,
                                         sov=sov, retainage_rate=RETAINAGE))
            vendors[sub_id] = dict(id=sub_id, legal_name=name, email_domain=domain,
                                   ein=f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
                                   bank_account=f"{rng.randint(10**8, 10**9 - 1)}:{rng.randint(10**9, 10**10 - 1)}",
                                   first_paid=None, crs_status="verified")
            exp = date(2027, rng.randint(1, 12), 28)
            out["cois"].append(COI(id=f"coi_{sub_id}", sub_id=sub_id, gl_expiry=exp,
                                   wc_expiry=exp - timedelta(days=rng.randint(0, 60)),
                                   additional_insured=True))
        out["subcontracts"] += contracts
        sub_total = sum(s.scheduled_value for c in contracts for s in c.sov)
        out["jobs"].append(Job(id=job_id, name=job_name, owner=owner, lender="First Pacific Bank",
                               architect="Okafor + Lin Architects",
                               contract_sum=money(sub_total * Decimal("1.15")),
                               retainage_rate=RETAINAGE, pay_when_paid=True))

        # per line: start month and monthly % gained, so progress is monotone and capped at 100
        curve = {(c.sub_id, l.item_no): (0 if k == 0 else rng.randint(0, 2), rng.randint(6, 16))
                 for c in contracts for k, l in enumerate(c.sov)}
        prev_lines = {c.sub_id: {l.item_no: Decimal(0) for l in c.sov} for c in contracts}
        prev_earned = {c.sub_id: Decimal(0) for c in contracts}

        for m, period in enumerate(PERIODS):
            period_end = (period + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            next_month = period_end + timedelta(days=1)
            submitted = datetime(period.year, period.month, 25, rng.randint(8, 17), rng.randint(0, 59))
            this_month = m == len(PERIODS) - 1
            job_due = Decimal(0)
            estimates = {}
            for c in contracts:
                lines = []
                for l in c.sov:
                    start, rate = curve[(c.sub_id, l.item_no)]
                    pct = Decimal(min(100, max(0, (m - start + 1) * rate)))
                    total = money(l.scheduled_value * pct / 100)
                    prev = prev_lines[c.sub_id][l.item_no]
                    lines.append(LineItem(
                        item_no=l.item_no, description=l.description, scheduled_value=l.scheduled_value,
                        previous_applications=prev, this_period=total - prev, stored_materials=money(0),
                        total_to_date=total, pct_complete=pct, balance_to_finish=l.scheduled_value - total,
                        retainage=money(total * c.retainage_rate)))
                    prev_lines[c.sub_id][l.item_no] = total
                    seen = pct + rng.randint(-3, 3) if 0 < pct < 100 else pct
                    estimates[l.item_no] = Decimal(min(100, max(0, seen)))
                contract_sum = sum(l.scheduled_value for l in c.sov)
                completed = sum(l.total_to_date for l in lines)
                retainage = sum(l.retainage for l in lines)
                earned = completed - retainage
                g702 = G702(original_contract_sum=contract_sum, net_change_orders=money(0),
                            contract_sum_to_date=contract_sum, total_completed_stored=completed,
                            retainage=retainage, total_earned_less_retainage=earned,
                            less_previous_certificates=prev_earned[c.sub_id],
                            current_payment_due=earned - prev_earned[c.sub_id])
                prev_earned[c.sub_id] = earned
                job_due += g702.current_payment_due
                v = vendors[c.sub_id]
                req = PayRequest(id=f"pr_{c.sub_id}_{m + 1}", number=m + 1, sub_id=c.sub_id, job_id=job_id,
                                 period_end=period_end, lines=lines, g702=g702,
                                 bank_account=v["bank_account"], submitted_at=submitted,
                                 source_doc=f"g703/{c.sub_id}_{period:%Y%m}.xlsx")
                out["pay_requests"].append(req)
                out["releases"].append(Release(
                    id=f"rel_c_{req.id}", sub_id=c.sub_id, job_id=job_id, kind=ReleaseKind.conditional_progress,
                    through_date=period_end, amount=g702.current_payment_due, signed=True,
                    received_at=submitted.date()))
                if not this_month:  # paid on the 10th; unconditional waiver arrives after it clears
                    paid = next_month.replace(day=10)
                    v["first_paid"] = v["first_paid"] or paid
                    out["ledger"].append(LedgerEntry(
                        id=f"pay_{req.id}", job_id=job_id, kind=LedgerKind.sub_payment,
                        amount=g702.current_payment_due, date=paid, period_end=period_end,
                        sub_id=c.sub_id, request_id=req.id, bank_account=v["bank_account"]))
                    out["releases"].append(Release(
                        id=f"rel_u_{req.id}", sub_id=c.sub_id, job_id=job_id,
                        kind=ReleaseKind.unconditional_progress, through_date=period_end,
                        amount=g702.current_payment_due, signed=True, received_at=paid + timedelta(days=5)))
            # owner's draw for the period clears on the 5th, before subs are paid
            out["ledger"].append(LedgerEntry(
                id=f"draw_{job_id}_{m + 1}", job_id=job_id, kind=LedgerKind.owner_draw,
                amount=money(job_due * Decimal("1.15")), date=next_month.replace(day=5), period_end=period_end))
            out["site_evidence"].append(SiteEvidence(
                id=f"site_{job_id}_{m + 1}", job_id=job_id, date=period.replace(day=22),
                frames=[f"frames/{job_id}_{period:%Y%m}_{i}.jpg" for i in range(3)], estimates=estimates))

    # suppliers that filed a notice to owner and then released: clean, but rule 7 has work to do
    for sub_id, supplier in [("sub_01", "Westside Ready-Mix"), ("sub_06", "Valley Aggregate"),
                             ("sub_11", "Pacific Block Supply")]:
        job_id = next(c.job_id for c in out["subcontracts"] if c.sub_id == sub_id)
        out["lower_tier_notices"].append(LowerTierNotice(
            id=f"nto_{sub_id}", supplier=supplier, sub_id=sub_id, job_id=job_id,
            notice_date=date(2026, 6, 12), released=True))

    out["vendors"] = [Vendor(**v) for v in vendors.values()]
    return out


def write(data: dict[str, list], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as db:
        for table, rows in data.items():
            db.execute(f"CREATE TABLE {table} (data TEXT NOT NULL)")
            db.executemany(f"INSERT INTO {table} VALUES (?)", [(r.model_dump_json(),) for r in rows])


def load(path: Path = Path("data/seed.db")) -> dict[str, list[dict]]:
    """Rows as dicts; validate with the matching model, e.g. PayRequest.model_validate(row)."""
    with sqlite3.connect(path) as db:
        tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [json.loads(d) for (d,) in db.execute(f"SELECT data FROM {t}")] for t in tables}


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/seed.db")
    data = generate()
    write(data, path)
    print(f"wrote {path}: " + ", ".join(f"{len(v)} {k}" for k, v in data.items()))
