"""Facts for the judgment questions, computed in code from the kernel's state slice.

Jev does no arithmetic, so every difference, match and count it needs is worked
out here and handed over as a value. Amounts and percentages are derived from
the billed dollars and the ledger, never taken from the sub's own summary
figures; where the sub's figure differs, both are given with a flag. Pure
functions, no I/O. `state` is a kernel.state.KernelState (or anything with the
same attributes).
"""

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal

from shared.schema import LedgerKind, PayRequest, ReleaseKind

MAX_STATE_BYTES = 24 * 1024  # Jev caps a request at 32 KiB; leave room for the questions
EMAIL_BODY_CHARS = 600
LABEL_CHARS = 80
MAX_EMAILS = 5
MAX_PRIOR = 6
SIMILAR = Decimal("0.01")  # two amounts within 1% count as "the same" for duplicate detection

# Characters a lookalike domain swaps in (spec: capital I for l, rn for m, 0 for o).
_CONFUSABLES = [("rn", "m"), ("vv", "w"), ("0", "o"), ("1", "l"), ("i", "l")]


# --- helpers -----------------------------------------------------------------


def _money(d: Decimal) -> str:
    return f"{d:.2f}"


def _pct(d: Decimal) -> str:
    return f"{d:.2f}"


def _utc(dt: datetime) -> datetime:
    """Naive datetimes are taken as UTC so mixed sources compare instead of raising."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _days(a: date, b: date) -> int:
    return (a - b).days


def _norm_account(account: str | None) -> str:
    return re.sub(r"[\W_]", "", account or "").lower()


def _mask(account: str | None) -> str | None:
    return None if account is None else f"...{_norm_account(account)[-4:]}"


def _near(a: Decimal, b: Decimal) -> bool:
    scale = max(abs(a), abs(b))
    return abs(a - b) <= SIMILAR * scale if scale else True


# --- untrusted text ----------------------------------------------------------

# A run of digits joined by up to three separator characters of any kind
# ("447712782:5071050724", "507.105.0724", "5071_0507_24", "507 – 105 – 0724", line breaks).
_DIGIT_RUN = re.compile(r"\d(?:[\W_]{0,3}\d)+")
_KEEP = [
    re.compile(r"\d{4}-\d{2}-\d{2}"),  # ISO date
    re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}"),  # US date
    re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2}"),  # a dollar amount
]


def redact(text: str) -> str:
    """Mask anything account-like (6+ digits in one run) to its last four digits."""

    def mask(m: re.Match) -> str:
        run = m.group()
        if any(k.fullmatch(run) for k in _KEEP):
            return run
        digits = re.sub(r"\D", "", run)
        return f"...{digits[-4:]}" if len(digits) >= 6 else run

    return _DIGIT_RUN.sub(mask, text)


def _label(text: str) -> str:
    return redact(" ".join(text.split()))[:LABEL_CHARS]


# --- domains -------------------------------------------------------------------


def _fold(domain: str) -> str:
    d = unicodedata.normalize("NFKC", domain).strip().lower().rstrip(".")
    for fake, real in _CONFUSABLES:
        d = d.replace(fake, real)
    return d


def _name_and_tld(domain: str) -> tuple[str, str]:
    labels = domain.split(".")
    return (labels[-2], labels[-1]) if len(labels) >= 2 else (domain, "")


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def domain_resembles(sender: str, vendor: str) -> str | None:
    """How `sender` imitates `vendor`, or None. The same domain in another case is not an imitation."""
    s, v = sender.strip().lower().rstrip("."), vendor.strip().lower().rstrip(".")
    if s == v:
        return None
    if not s.isascii():
        return "non_ascii_characters"
    fs, fv = _fold(s), _fold(v)
    if fs == fv:
        return "confusable_characters"
    s_name, s_tld = _name_and_tld(fs)
    v_name, v_tld = _name_and_tld(fv)
    if v_name and v_name in fs.split(".")[:-2]:
        return "vendor_name_in_subdomain"
    if s_name == v_name and s_tld != v_tld:
        return "different_top_level_domain"
    if s_name.replace("-", "") == v_name.replace("-", ""):
        return "added_or_removed_hyphen"
    if len(v_name) >= 5 and _edit_distance(s_name, v_name) <= 2:
        return "one_or_two_letters_changed"
    return None


def is_lookalike(sender: str, vendor: str) -> bool:
    return domain_resembles(sender, vendor) is not None


# --- sections ------------------------------------------------------------------


def _limit(state) -> Decimal | None:
    value = state.contract.get("auto_release_limit_usd")
    return None if value is None else Decimal(str(value))


def _paid_to_sub(state, request: PayRequest) -> Decimal:
    return sum(
        (e.amount for e in state.ledger
         if e.kind == LedgerKind.sub_payment and e.sub_id == request.sub_id and e.job_id == request.job_id
         and e.request_id != request.id),
        Decimal("0"),
    )


def computed_payment_due(state, request: PayRequest) -> Decimal:
    """(Work billed to date - retainage at the contract rate) - what the ledger shows was already paid."""
    completed = sum((l.total_to_date for l in request.lines), Decimal("0"))
    retainage = (completed * state.subcontract.retainage_rate).quantize(Decimal("0.01"))
    return completed - retainage - _paid_to_sub(state, request)


def request_facts(state, request: PayRequest) -> dict:
    limit = _limit(state)
    printed = request.g702.current_payment_due
    computed = computed_payment_due(state, request)
    return {
        "number": request.number,
        "period_end": request.period_end.isoformat(),
        "payment_due_computed": _money(computed),
        "payment_due_on_sub_summary": _money(printed),
        "sub_summary_differs_from_computed": abs(printed - computed) > Decimal("0.01"),
        "auto_release_limit": _money(limit) if limit is not None else None,
        "over_auto_release_limit": max(printed, computed) > limit if limit is not None else None,
    }


def vendor_facts(state) -> dict:
    v = state.vendor
    return {
        "legal_name": _label(v.legal_name),
        "identity_check": v.crs_status,
        "first_payment_to_vendor": v.first_paid is None,
        "first_paid": v.first_paid.isoformat() if v.first_paid else None,
    }


def release_facts(state, request: PayRequest) -> dict:
    final_stage = all(line.balance_to_finish == 0 for line in request.lines)
    required = ReleaseKind.conditional_final if final_stage else ReleaseKind.conditional_progress
    due = computed_payment_due(state, request)
    mine = sorted(
        (r for r in state.releases if r.sub_id == request.sub_id and r.job_id == request.job_id),
        key=lambda r: (r.through_date, r.signed, r.kind == required),
        reverse=True,
    )

    def describe(r):
        return {
            "kind": r.kind.value,
            "kind_matches_required": r.kind == required,
            "through_date": r.through_date.isoformat(),
            "through_date_minus_period_end_days": _days(r.through_date, request.period_end),
            "signed": r.signed,
            "amount_minus_payment_due": _money(r.amount - due),
        }

    covering = [r for r in mine if r.through_date == request.period_end]
    others = [r for r in mine if r.through_date != request.period_end]
    return {
        "payment_stage": "final" if final_stage else "progress",
        "required_kind": required.value,
        "releases_through_this_period": [describe(r) for r in covering[:3]],
        "signed_release_of_required_kind_through_this_period": any(r.signed and r.kind == required for r in covering),
        "most_recent_other_release": describe(others[0]) if others else None,
    }


def _names_account(e, request: PayRequest) -> bool:
    return e.claims.new_account is not None and _norm_account(e.claims.new_account) == _norm_account(request.bank_account)


def bank_facts(state, request: PayRequest) -> dict:
    vendor = state.vendor
    on_file = state.known_bank_account or vendor.bank_account
    paid_accounts = [_norm_account(e.bank_account) for e in sorted(state.ledger, key=lambda e: e.date)
                     if e.sub_id == request.sub_id and e.bank_account]
    changes = sum(1 for a, b in zip(paid_accounts, paid_accounts[1:]) if a != b)

    vdomain = vendor.email_domain.strip().lower()

    def exact(e) -> bool:
        return e.from_domain.strip().lower() == vdomain

    ordinary = sorted((_utc(e.sent_at) for e in state.emails if exact(e) and not e.claims.bank_change), reverse=True)
    # Most relevant first (bank-change requests, other senders), then newest, so routine mail can't crowd them out.
    emails = sorted(
        state.emails,
        key=lambda e: (e.claims.bank_change or e.claims.new_account is not None, not exact(e), _utc(e.sent_at)),
        reverse=True,
    )
    out = []
    for e in emails[:MAX_EMAILS]:
        sent = _utc(e.sent_at)
        before = [o for o in ordinary if o < sent]
        out.append({
            "from_domain": e.from_domain,
            "domain_matches_vendor_exactly": exact(e),
            "domain_imitates_vendor": domain_resembles(e.from_domain, vendor.email_domain),
            "asks_for_bank_change": e.claims.bank_change,
            "names_this_requests_account": _names_account(e, request),
            "urgency_language": e.claims.urgency,
            "days_since_vendor_last_ordinary_email": (sent - before[0]).days if before else None,
            "days_before_request_submitted": (_utc(request.submitted_at) - sent).days,
            "subject": redact(e.subject)[:200],
            "body_excerpt": redact(e.body)[:EMAIL_BODY_CHARS],
        })
    return {
        "vendor_domain": vendor.email_domain,
        "account_on_file": _mask(on_file),
        "account_on_request": _mask(request.bank_account),
        "account_changed": _norm_account(request.bank_account) != _norm_account(on_file),
        "prior_account_changes": changes,
        "emails_asking_for_bank_change": sum(1 for e in state.emails if e.claims.bank_change),
        "emails_from_other_domains": sum(1 for e in state.emails if not exact(e)),
        "emails_naming_this_account": sum(1 for e in state.emails if _names_account(e, request)),
        "emails": out,
        "emails_omitted": max(0, len(emails) - MAX_EMAILS),
    }


def _previous_period(state, request: PayRequest) -> PayRequest | None:
    """The latest paid request for an earlier period, by date rather than by the sub's own numbering."""
    earlier = [r for r in state.prior_requests if r.period_end < request.period_end]
    return max(earlier, key=lambda r: r.period_end, default=None)


def _billed_pct(line) -> Decimal | None:
    return line.total_to_date / line.scheduled_value * 100 if line.scheduled_value else None


def progress_facts(state, request: PayRequest) -> dict:
    tolerance = state.contract.get("progress_tolerance_points")
    tol = Decimal(str(tolerance)) if tolerance is not None else None
    prev = _previous_period(state, request)
    prev_pct = {l.item_no: _billed_pct(l) for l in prev.lines} if prev else {}
    ev = state.site_evidence
    lines = []
    for l in request.lines:
        billed = _billed_pct(l)  # what the dollars bill, whatever the % column says
        seen = ev.estimates.get(l.item_no) if ev else None
        gap = billed - seen if billed is not None and seen is not None else None
        last = prev_pct.get(l.item_no)
        lines.append({
            "item_no": l.item_no,
            "description": _label(l.description),
            "billed_pct": _pct(billed) if billed is not None else None,
            "percent_column_disagrees_with_billed": billed is not None and abs(billed - l.pct_complete) > 1,
            "last_period_billed_pct": _pct(last) if last is not None else None,
            "change_points": _pct(billed - last) if billed is not None and last is not None else None,
            "video_pct": str(seen) if seen is not None else None,
            "billed_minus_video_points": _pct(gap) if gap is not None else None,
            "gap_exceeds_tolerance": abs(gap) > tol if gap is not None and tol is not None else None,
        })
    return {
        "tolerance_points": tolerance,
        "site_evidence_date": ev.date.isoformat() if ev else None,
        "days_from_video_to_period_end": _days(request.period_end, ev.date) if ev else None,
        "previous_paid_period_end": prev.period_end.isoformat() if prev else None,
        "days_since_previous_paid_period": _days(request.period_end, prev.period_end) if prev else None,
        "lines": lines,
    }


def duplicate_facts(state, request: PayRequest) -> dict:
    paid = {r.id for r in state.prior_requests}
    mine = {l.item_no: l for l in request.lines}
    ranked = []
    for r in state.all_requests:
        if r.id == request.id:
            continue
        theirs = {l.item_no: l for l in r.lines}
        shared = mine.keys() & theirs.keys()
        # Cumulative totals only grow month to month, so matching totals means no new work was billed.
        same_totals = sum(1 for k in shared if _near(mine[k].total_to_date, theirs[k].total_to_date))
        same_this_period = sum(1 for k in shared if _near(mine[k].this_period, theirs[k].this_period))
        entry = {
            "number": r.number,
            "period_end": r.period_end.isoformat(),
            "same_number": r.number == request.number,
            "same_period": r.period_end == request.period_end,
            "lines_with_same_total_to_date": f"{same_totals} of {len(request.lines)}",
            "every_line_same_total_to_date": same_totals == len(request.lines) == len(r.lines),
            "lines_with_same_this_period": f"{same_this_period} of {len(request.lines)}",
            "already_paid": r.id in paid,
            "days_between_submissions": (_utc(request.submitted_at) - _utc(r.submitted_at)).days,
        }
        ranked.append(((same_totals, r.period_end == request.period_end, same_this_period), entry))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return {
        "this_number": request.number,
        "highest_earlier_number": max((r.number for r in state.all_requests if r.id != request.id), default=None),
        "earlier_requests": [e for _, e in ranked[:MAX_PRIOR]],
        "earlier_requests_omitted": max(0, len(ranked) - MAX_PRIOR),
    }


def contract_facts(state, request: PayRequest) -> dict:
    """Inputs to the kernel's other checks, so the overall question sees what the rules see."""
    rate = state.subcontract.retainage_rate
    sov = {s.item_no: s.scheduled_value for s in state.subcontract.sov}
    approved: dict[str, Decimal] = {}
    for co in state.change_orders:
        if co.approved:
            approved[co.item_no] = approved.get(co.item_no, Decimal("0")) + co.amount
    cent = Decimal("0.01")
    coi = state.coi
    draws = [e for e in state.ledger if e.kind == LedgerKind.owner_draw and e.period_end == request.period_end]
    return {
        "contract_retainage_rate": str(rate),
        "lines_withholding_less_than_contract_retainage": [
            l.item_no for l in request.lines if l.retainage < (l.total_to_date * rate).quantize(cent) - cent],
        "lines_billed_past_scheduled_value_plus_approved_change_orders": [
            l.item_no for l in request.lines
            if l.total_to_date > sov.get(l.item_no, l.scheduled_value) + approved.get(l.item_no, Decimal("0"))],
        "lines_not_in_subcontract": [l.item_no for l in request.lines if l.item_no not in sov],
        "insurance_on_file": coi is not None,
        "general_liability_expired_by_period_end": coi.gl_expiry < request.period_end if coi else None,
        "workers_comp_expired_by_period_end": coi.wc_expiry < request.period_end if coi else None,
        "supplier_notices_not_released": sum(1 for n in state.lower_tier_notices if not n.released),
        "owner_draw_received_for_period": bool(draws),
        "owner_draw_date": max(e.date for e in draws).isoformat() if draws else None,
    }


def compute_facts(state, request: PayRequest) -> dict:
    facts = {
        "request": request_facts(state, request),
        "vendor": vendor_facts(state),
        "contract": contract_facts(state, request),
        "release": release_facts(state, request),
        "bank": bank_facts(state, request),
        "progress": progress_facts(state, request),
        "duplicates": duplicate_facts(state, request),
    }
    facts = json.loads(json.dumps(facts, default=str))  # plain JSON, whatever types the contract holds
    # Shrink the free text first; never drop computed facts silently.
    for limit in (EMAIL_BODY_CHARS, 200, 0):
        if len(json.dumps(facts).encode()) <= MAX_STATE_BYTES:
            break
        for e in facts["bank"]["emails"]:
            e["body_excerpt"] = e["body_excerpt"][:limit]
    if len(json.dumps(facts).encode()) > MAX_STATE_BYTES:
        raise ValueError("judgment state exceeds Jev's request size limit")
    return facts
