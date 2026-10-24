"""Facts for the judgment questions, computed in code from the kernel's state slice.

Jev does no arithmetic, so every difference, match and count it needs is worked
out here and handed over as a value. Pure functions, no I/O. `state` is a
kernel.state.KernelState (or anything with the same attributes).
"""

import json
import re
from datetime import date
from decimal import Decimal

from shared.schema import PayRequest, ReleaseKind

MAX_STATE_BYTES = 24 * 1024  # Jev caps a request at 32 KiB; leave room for the questions
EMAIL_BODY_CHARS = 600
LABEL_CHARS = 80
MAX_EMAILS = 5
MAX_PRIOR = 6

# Characters a lookalike domain swaps in (spec: capital I for l, rn for m, 0 for o).
_CONFUSABLES = [("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("i", "l")]


def _money(d: Decimal) -> str:
    return f"{d:.2f}"


def _days(a: date, b: date) -> int:
    return (a - b).days


def domain_skeleton(domain: str) -> str:
    s = domain.strip().lower()
    for fake, real in _CONFUSABLES:
        s = s.replace(fake, real)
    return s


def is_lookalike(sender: str, vendor: str) -> bool:
    """Different domain that reads the same once confusable characters are folded."""
    return sender.strip().lower() != vendor.strip().lower() and domain_skeleton(sender) == domain_skeleton(vendor)


def _mask(account: str | None) -> str | None:
    return None if account is None else f"...{account[-4:]}"


_LONG_NUMBER = re.compile(r"\d[\d:\- ]{4,}\d")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def redact(text: str) -> str:
    """Mask account-like digit runs in free text; the matching itself is done in code."""
    def mask(m: re.Match) -> str:
        if _ISO_DATE.fullmatch(m.group().strip()):
            return m.group()
        digits = re.sub(r"\D", "", m.group())
        return f"...{digits[-4:]}" if len(digits) >= 6 else m.group()
    return _LONG_NUMBER.sub(mask, text)


def _label(text: str) -> str:
    return " ".join(text.split())[:LABEL_CHARS]


def _limit(state) -> Decimal | None:
    value = state.contract.get("auto_release_limit_usd")
    return None if value is None else Decimal(str(value))


def release_facts(state, request: PayRequest) -> dict:
    final_stage = all(line.balance_to_finish == 0 for line in request.lines)
    mine = sorted(
        (r for r in state.releases if r.sub_id == request.sub_id and r.job_id == request.job_id),
        key=lambda r: r.through_date,
        reverse=True,
    )
    covering = [r for r in mine if r.through_date == request.period_end]
    pick = covering[0] if covering else (mine[0] if mine else None)
    facts = {
        "payment_stage": "final" if final_stage else "progress",
        "required_kind": ReleaseKind.conditional_final.value if final_stage else ReleaseKind.conditional_progress.value,
        "release_on_file": pick is not None,
        "releases_through_this_period": len(covering),
    }
    if pick is not None:
        due = request.g702.current_payment_due
        facts.update(
            kind=pick.kind.value,
            kind_matches_required=pick.kind.value == facts["required_kind"],
            through_date=pick.through_date.isoformat(),
            through_date_vs_period_end_days=_days(pick.through_date, request.period_end),
            signed=pick.signed,
            amount=_money(pick.amount),
            amount_minus_payment_due=_money(pick.amount - due),
        )
    return facts


def bank_facts(state, request: PayRequest) -> dict:
    vendor = state.vendor
    on_file = state.known_bank_account or vendor.bank_account
    paid_accounts = [e.bank_account for e in sorted(state.ledger, key=lambda e: e.date)
                     if e.sub_id == request.sub_id and e.bank_account]
    changes = sum(1 for a, b in zip(paid_accounts, paid_accounts[1:]) if a != b)

    emails = sorted(state.emails, key=lambda e: e.sent_at, reverse=True)
    ordinary = [e for e in emails if e.from_domain.lower() == vendor.email_domain.lower() and not e.claims.bank_change]
    out = []
    for e in emails[:MAX_EMAILS]:
        prior_ordinary = [o for o in ordinary if o.sent_at < e.sent_at]
        out.append({
            "from_domain": e.from_domain,
            "domain_matches_vendor_exactly": e.from_domain.lower() == vendor.email_domain.lower(),
            "domain_is_lookalike_of_vendor": is_lookalike(e.from_domain, vendor.email_domain),
            "asks_for_bank_change": e.claims.bank_change,
            "new_account_is_this_requests_account": e.claims.new_account is not None
            and e.claims.new_account == request.bank_account,
            "urgency_language": e.claims.urgency,
            "days_since_vendor_last_ordinary_email": _days(e.sent_at.date(), prior_ordinary[0].sent_at.date())
            if prior_ordinary else None,
            "days_before_request_submitted": _days(request.submitted_at.date(), e.sent_at.date()),
            "subject": redact(e.subject)[:200],
            "body_excerpt": redact(e.body)[:EMAIL_BODY_CHARS],
        })
    return {
        "vendor_domain": vendor.email_domain,
        "account_on_file": _mask(on_file),
        "account_on_request": _mask(request.bank_account),
        "account_changed": request.bank_account != on_file,
        "prior_account_changes": changes,
        "emails": out,
        "emails_omitted": max(0, len(emails) - MAX_EMAILS),
    }


def _prior(state, request: PayRequest) -> PayRequest | None:
    earlier = [r for r in state.prior_requests if r.number < request.number]
    return max(earlier, key=lambda r: r.number, default=None)


def progress_facts(state, request: PayRequest) -> dict:
    tolerance = state.contract.get("progress_tolerance_points")
    prior = _prior(state, request)
    prior_pct = {l.item_no: l.pct_complete for l in prior.lines} if prior else {}
    ev = state.site_evidence
    lines = []
    for l in request.lines:
        seen = ev.estimates.get(l.item_no) if ev else None
        gap = l.pct_complete - seen if seen is not None else None
        lines.append({
            "item_no": l.item_no,
            "description": _label(l.description),
            "claimed_pct": str(l.pct_complete),
            "last_month_pct": str(prior_pct[l.item_no]) if l.item_no in prior_pct else None,
            "change_points": str(l.pct_complete - prior_pct[l.item_no]) if l.item_no in prior_pct else None,
            "video_pct": str(seen) if seen is not None else None,
            "claimed_minus_video_points": str(gap) if gap is not None else None,
            "gap_exceeds_tolerance": abs(gap) > Decimal(str(tolerance)) if gap is not None and tolerance is not None else None,
        })
    return {
        "tolerance_points": tolerance,
        "site_evidence_date": ev.date.isoformat() if ev else None,
        "days_from_video_to_period_end": _days(request.period_end, ev.date) if ev else None,
        "days_since_last_period": _days(request.period_end, prior.period_end) if prior else None,
        "lines": lines,
    }


def duplicate_facts(state, request: PayRequest) -> dict:
    paid = {r.id for r in state.prior_requests}
    mine = {(l.item_no, l.total_to_date, l.this_period) for l in request.lines}
    others = []
    for r in state.all_requests:
        if r.id == request.id:
            continue
        theirs = {(l.item_no, l.total_to_date, l.this_period) for l in r.lines}
        same = len(mine & theirs)
        others.append({
            "number": r.number,
            "period_end": r.period_end.isoformat(),
            "same_number": r.number == request.number,
            "same_period": r.period_end == request.period_end,
            "lines_identical": f"{same} of {len(request.lines)}",
            "all_lines_identical": same == len(request.lines) == len(r.lines),
            "same_payment_due": r.g702.current_payment_due == request.g702.current_payment_due,
            "already_paid": r.id in paid,
            "days_between_submissions": _days(request.submitted_at.date(), r.submitted_at.date()),
        })
    # Most similar first, so truncation drops the least relevant.
    others.sort(key=lambda o: (o["all_lines_identical"], o["same_period"], o["same_payment_due"]), reverse=True)
    return {
        "this_number": request.number,
        "highest_earlier_number": max((r.number for r in state.prior_requests), default=None),
        "earlier_requests": others[:MAX_PRIOR],
        "earlier_requests_omitted": max(0, len(others) - MAX_PRIOR),
    }


def request_facts(state, request: PayRequest) -> dict:
    limit = _limit(state)
    due = request.g702.current_payment_due
    return {
        "number": request.number,
        "period_end": request.period_end.isoformat(),
        "payment_due": _money(due),
        "auto_release_limit": _money(limit) if limit is not None else None,
        "over_auto_release_limit": due > limit if limit is not None else None,
    }


def vendor_facts(state) -> dict:
    v = state.vendor
    return {
        "legal_name": v.legal_name,
        "identity_check": v.crs_status,
        "first_payment_to_vendor": v.first_paid is None,
        "first_paid": v.first_paid.isoformat() if v.first_paid else None,
    }


def compute_facts(state, request: PayRequest) -> dict:
    facts = {
        "request": request_facts(state, request),
        "vendor": vendor_facts(state),
        "release": release_facts(state, request),
        "bank": bank_facts(state, request),
        "progress": progress_facts(state, request),
        "duplicates": duplicate_facts(state, request),
    }
    # Shrink the free text first; never drop computed facts silently.
    for limit in (EMAIL_BODY_CHARS, 200, 0):
        if len(json.dumps(facts).encode()) <= MAX_STATE_BYTES:
            break
        for e in facts["bank"]["emails"]:
            e["body_excerpt"] = e["body_excerpt"][:limit]
    if len(json.dumps(facts).encode()) > MAX_STATE_BYTES:
        raise ValueError("judgment state exceeds Jev's request size limit")
    return facts
