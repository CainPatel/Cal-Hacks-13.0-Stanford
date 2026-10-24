"""The 25 planted anomalies from the spec, each a labeled case on top of the clean seed. Owner: B.

A mutator takes the seed data and returns (new data, Label). It never touches its input.
Every case targets an October (this month, unpaid) request, so nothing downstream breaks.
The sub's paperwork stays internally consistent; only the facts are wrong.
The fuzzer reuses these mutators with random targets.

Label.rules = rules that must fail to catch it. Other rules may fail too.
"""
import random
from datetime import date, datetime
from decimal import Decimal

from seed.generate import RETAINAGE, generate, money
from shared.schema import (
    COI, Action, ChangeOrder, Email, EmailClaims, Label, LineItem, LowerTierNotice, PayRequest,
    Release, ReleaseKind, SOVLine, Subcontract, Vendor,
)

THIS_MONTH = 6
OCT_31 = date(2026, 10, 31)


# --- helpers ----------------------------------------------------------------

def copy(data):
    return {k: list(v) for k, v in data.items()}


def find(rows, id_):
    return next(i for i, r in enumerate(rows) if r.id == id_)


def update(rows, id_, **fields):
    i = find(rows, id_)
    rows[i] = rows[i].model_copy(update=fields)
    return rows[i]


def remove(rows, id_):
    del rows[find(rows, id_)]


def oct_request(data, sub_id) -> PayRequest:
    return data["pay_requests"][find(data["pay_requests"], f"pr_{sub_id}_{THIS_MONTH}")]


def rebill(req: PayRequest, pcts: dict[str, Decimal] = {}, retainage_rate: Decimal = RETAINAGE) -> PayRequest:
    """Re-claim some lines at new percents and redo every total, like a sub filling out the form."""
    lines = []
    for l in req.lines:
        pct = Decimal(pcts.get(l.item_no, l.pct_complete))
        total = money(l.scheduled_value * pct / 100)
        lines.append(l.model_copy(update=dict(
            pct_complete=pct, total_to_date=total,
            this_period=total - l.previous_applications - l.stored_materials,
            balance_to_finish=l.scheduled_value - total, retainage=money(total * retainage_rate))))
    g = req.g702
    completed = sum(l.total_to_date for l in lines)
    retainage = sum(l.retainage for l in lines)
    g702 = g.model_copy(update=dict(
        total_completed_stored=completed, retainage=retainage,
        total_earned_less_retainage=completed - retainage,
        current_payment_due=completed - retainage - g.less_previous_certificates))
    return req.model_copy(update=dict(lines=lines, g702=g702))


def put_request(data, req):
    rows = data["pay_requests"]
    try:
        rows[find(rows, req.id)] = req
    except StopIteration:
        rows.append(req)


def label(req, anomaly, *rules):
    return Label(request_id=req.id, anomaly=anomaly, expected_action=Action.hold, rules=list(rules))


def confusable(domain):
    """Lookalike domain: capital I for l, rn for m, or zero for o (first one that applies)."""
    name, _, tld = domain.rpartition(".")
    for real, fake in [("l", "I"), ("m", "rn"), ("o", "0")]:
        if real in name:
            return f"{name.replace(real, fake, 1)}.{tld}"
    raise ValueError(f"no confusable for {domain}")


def account(rng):
    return f"{rng.randint(10**8, 10**9 - 1)}:{rng.randint(10**9, 10**10 - 1)}"


# --- mutators: one per anomaly in the spec ----------------------------------

def overbill_pct(data, sub_id, rng, item_no=None, claim=None, video=None):
    """Claim more % on one line than the site video shows. Rule 13. None if no line has room."""
    data = copy(data)
    req = oct_request(data, sub_id)
    site_id = f"site_{req.job_id}_{THIS_MONTH}"
    site = data["site_evidence"][find(data["site_evidence"], site_id)]
    line = next(l for l in req.lines if l.item_no == item_no) if item_no else min(req.lines, key=lambda l: l.pct_complete)
    claim = Decimal(claim if claim is not None else min(100, line.pct_complete + rng.randint(15, 35)))
    seen = Decimal(video) if video is not None else site.estimates[line.item_no]
    if claim - seen <= 12:  # too close to the video to be a clear over-bill (tolerance is 10)
        return None
    req = rebill(req, {line.item_no: claim})
    put_request(data, req)
    if video is not None:
        update(data["site_evidence"], site_id, estimates={**site.estimates, line.item_no: seen})
    return data, label(req, "overbill_pct", "rule_13")


def retainage_slip(data, sub_id, rng, rate="0.05"):
    """Withhold less retainage than the contract rate. Rule 3."""
    data = copy(data)
    req = rebill(oct_request(data, sub_id), retainage_rate=Decimal(rate))
    put_request(data, req)
    return data, label(req, "retainage_slip", "rule_03")


def missing_conditional_release(data, sub_id, rng, stale=False):
    """No conditional waiver for this period, or last month's attached instead. Rule 4."""
    data = copy(data)
    req = oct_request(data, sub_id)
    rel_id = f"rel_c_{req.id}"
    if stale:
        prev = next(r for r in data["releases"] if r.id == f"rel_c_pr_{sub_id}_{THIS_MONTH - 1}")
        update(data["releases"], rel_id, through_date=prev.through_date, amount=prev.amount)
    else:
        remove(data["releases"], rel_id)
    return data, label(req, "missing_conditional_release", "rule_04")


def expired_wc(data, sub_id, rng, expiry=date(2026, 10, 15)):
    """Workers' comp expires before the payment date. Rule 6."""
    data = copy(data)
    update(data["cois"], f"coi_{sub_id}", wc_expiry=expiry)
    return data, label(oct_request(data, sub_id), "expired_wc", "rule_06")


def past_sov_no_co(data, sub_id, rng, pct=108, unapproved_co=False):
    """Bill a line past its scheduled value with no approved change order. Rule 2."""
    data = copy(data)
    req = oct_request(data, sub_id)
    line = max(req.lines, key=lambda l: l.pct_complete)
    req = rebill(req, {line.item_no: Decimal(pct)})
    put_request(data, req)
    if unapproved_co:  # a CO exists for the overage but nobody approved it
        data["change_orders"].append(ChangeOrder(
            id=f"co_{sub_id}_1", job_id=req.job_id, sub_id=sub_id, item_no=line.item_no,
            amount=money(line.scheduled_value * Decimal("0.15")), approved=False, date=date(2026, 10, 3)))
    return data, label(req, "past_sov_no_co", "rule_02")


def duplicate_new_number(data, sub_id, rng, new_period=False):
    """Resubmit last month's paid request under a new number. Rule 12."""
    data = copy(data)
    paid = data["pay_requests"][find(data["pay_requests"], f"pr_{sub_id}_{THIS_MONTH - 1}")]
    dup = paid.model_copy(update=dict(
        id=f"pr_{sub_id}_{THIS_MONTH - 1}_resub", number=THIS_MONTH + 1,
        period_end=OCT_31 if new_period else paid.period_end,
        submitted_at=datetime(2026, 10, 26, 7, 41), source_doc=f"g703/{sub_id}_resubmit.pdf"))
    data["pay_requests"].append(dup)
    return data, label(dup, "duplicate_new_number", "rule_12")


def duplicate_this_month(data, sub_id, rng):
    """Submit this month's request a second time under a new number, so it gets paid twice. Rule 12."""
    data = copy(data)
    orig = oct_request(data, sub_id)
    dup = orig.model_copy(update=dict(id=f"{orig.id}_again", number=THIS_MONTH + 1,
                                      submitted_at=datetime(2026, 10, 27, 8, 0),
                                      source_doc=f"g703/{sub_id}_202610_v2.pdf"))
    data["pay_requests"].append(dup)
    return data, label(dup, "duplicate_this_month", "rule_12")


def missing_prior_unconditional(data, sub_id, rng):
    """Last month's payment cleared but its unconditional waiver never came in. Rule 5."""
    data = copy(data)
    remove(data["releases"], f"rel_u_pr_{sub_id}_{THIS_MONTH - 1}")
    return data, label(oct_request(data, sub_id), "missing_prior_unconditional", "rule_05")


def bank_change_email(data, sub_id, rng, homoglyph=False):
    """Email asks to pay a new account; the request uses it. Rule 10 (+11 if the domain is fake)."""
    data = copy(data)
    vendor = data["vendors"][find(data["vendors"], sub_id)]
    domain = confusable(vendor.email_domain) if homoglyph else vendor.email_domain
    new = account(rng)
    data["emails"].append(Email(
        id=f"em_{sub_id}_bank", from_addr=f"accounting@{domain}", from_domain=domain,
        sent_at=datetime(2026, 10, 22, 9, 14), subject="Updated remittance details",
        body=(f"Hi, we've moved our accounts to a new bank. Effective immediately please send all "
              f"payments to account {new}. We need this updated before Friday's payment run. Thanks!"),
        claims=EmailClaims(bank_change=True, new_account=new, urgency=True)))
    req = oct_request(data, sub_id).model_copy(update=dict(bank_account=new))
    put_request(data, req)
    rules = ("rule_10", "rule_11") if homoglyph else ("rule_10",)
    return data, label(req, "bank_change_homoglyph" if homoglyph else "bank_change_email", *rules)


def supplier_notice_unreleased(data, sub_id, rng, supplier="Bay Area Lumber Supply"):
    """A supplier filed a notice to owner this month and hasn't released. Rule 7."""
    data = copy(data)
    req = oct_request(data, sub_id)
    data["lower_tier_notices"].append(LowerTierNotice(
        id=f"nto_{sub_id}_oct", supplier=supplier, sub_id=sub_id, job_id=req.job_id,
        notice_date=date(2026, 10, 9), released=False))  # after September's decisions: only October is affected
    return data, label(req, "supplier_notice_unreleased", "rule_07")


def draw_not_cleared(data, sub_id, rng):
    """The owner's draw for this period hasn't landed (pay-when-paid). Rule 8."""
    data = copy(data)
    req = oct_request(data, sub_id)
    remove(data["ledger"], f"draw_{req.job_id}_{THIS_MONTH}")
    return data, label(req, "draw_not_cleared", "rule_08")


def new_sub_ein_mismatch(data, job_id, rng, name="Apex Concrete Services LLC", domain="apexconcretesvc.com"):
    """A brand-new sub whose EIN doesn't match its identity check. Rule 9."""
    data = copy(data)
    sub_id = f"sub_new_{job_id}"
    data["vendors"].append(Vendor(
        id=sub_id, legal_name=name, ein=f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",
        email_domain=domain, bank_account=account(rng), first_paid=None, crs_status="mismatch"))
    sov = [SOVLine(item_no=f"02-{k}0", description=d, scheduled_value=money(rng.randrange(800, 2500) * 100))
           for k, d in enumerate(["Demolition", "Site prep", "Haul-off"], 1)]
    data["subcontracts"].append(Subcontract(id=f"sc_{sub_id}", sub_id=sub_id, job_id=job_id,
                                            sov=sov, retainage_rate=RETAINAGE))
    data["cois"].append(COI(id=f"coi_{sub_id}", sub_id=sub_id, gl_expiry=date(2027, 6, 30),
                            wc_expiry=date(2027, 6, 30), additional_insured=True))
    zero = money(0)
    blank = [LineItem(item_no=s.item_no, description=s.description, scheduled_value=s.scheduled_value,
                      previous_applications=zero, this_period=zero, stored_materials=zero, total_to_date=zero,
                      pct_complete=Decimal(0), balance_to_finish=s.scheduled_value, retainage=zero) for s in sov]
    template = data["pay_requests"][0].g702
    req = PayRequest(
        id=f"pr_{sub_id}_1", number=1, sub_id=sub_id, job_id=job_id, period_end=OCT_31, lines=blank,
        g702=template.model_copy(update=dict(
            original_contract_sum=sum(s.scheduled_value for s in sov), net_change_orders=zero,
            contract_sum_to_date=sum(s.scheduled_value for s in sov), less_previous_certificates=zero)),
        bank_account=data["vendors"][-1].bank_account, submitted_at=datetime(2026, 10, 24, 15, 2),
        source_doc=f"g703/{sub_id}_202610.pdf")
    req = rebill(req, {s.item_no: Decimal(rng.randint(20, 45)) for s in sov})
    data["pay_requests"].append(req)
    data["releases"].append(Release(
        id=f"rel_c_{req.id}", sub_id=sub_id, job_id=job_id, kind=ReleaseKind.conditional_progress,
        through_date=OCT_31, amount=req.g702.current_payment_due, signed=True, received_at=date(2026, 10, 24)))
    return data, label(req, "new_sub_ein_mismatch", "rule_09")


# --- the planted set ----------------------------------------------------------

# The three demo stories, from the GC interview. Also baked into data/seed.db.
DEMO = [
    (overbill_pct, "sub_03", dict(item_no="09-20", claim=70, video=45)),  # Summit Drywall: 70% claimed, video ~45%
    (bank_change_email, "sub_04", dict(homoglyph=True)),                  # Brightline Electric: capital I for l
    (supplier_notice_unreleased, "sub_01", dict(supplier="Westside Ready-Mix")),  # Coastal Concrete's supplier
]

PLANTED = DEMO + [
    (overbill_pct, "sub_07", {}), (overbill_pct, "sub_11", {}), (overbill_pct, "sub_14", {}),
    (retainage_slip, "sub_02", {}), (retainage_slip, "sub_12", dict(rate="0")),
    (missing_conditional_release, "sub_05", {}), (missing_conditional_release, "sub_08", {}),
    (missing_conditional_release, "sub_13", dict(stale=True)),
    (expired_wc, "sub_06", {}), (expired_wc, "sub_15", dict(expiry=date(2026, 9, 30))),
    (past_sov_no_co, "sub_09", {}), (past_sov_no_co, "sub_10", dict(pct=112)),
    (past_sov_no_co, "sub_02", dict(pct=105, unapproved_co=True)),
    (duplicate_new_number, "sub_05", {}), (duplicate_new_number, "sub_12", dict(new_period=True)),
    (bank_change_email, "sub_09", {}), (bank_change_email, "sub_14", {}),
    (supplier_notice_unreleased, "sub_10", {}),
    (draw_not_cleared, "sub_06", {}), (draw_not_cleared, "sub_13", {}),
    (new_sub_ein_mismatch, "job_1", {}),
    (new_sub_ein_mismatch, "job_3", dict(name="Northgate Interiors Inc.", domain="northgateinteriors.com")),
]


def cases(data=None, rng_seed=7) -> list[tuple[dict, Label]]:
    """All 25 planted anomalies, each as its own (data, label). The kernel decides label.request_id.

    Cases are independent, so the same request id can appear in two cases (different breakage).
    Key results by case index, not request id.
    """
    data = data or generate()
    rng = random.Random(rng_seed)
    return [mutate(data, target, rng, **kw) for mutate, target, kw in PLANTED]


def with_demo(data, rng_seed=7) -> tuple[dict, list[Label]]:
    """Clean data with the three demo stories applied together, plus a label for every request."""
    rng = random.Random(rng_seed)
    labels = []
    for mutate, target, kw in DEMO:
        data, lab = mutate(data, target, rng, **kw)
        labels.append(lab)
    held = {l.request_id for l in labels}
    labels += [Label(request_id=r.id, anomaly=None, expected_action=Action.release, rules=[])
               for r in data["pay_requests"] if r.id not in held]
    data["labels"] = labels
    return data, labels
