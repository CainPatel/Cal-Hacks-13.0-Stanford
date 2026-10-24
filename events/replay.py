"""Load the seed into the log as facts, and replay months through the pipeline. Owner: B.

Month m is 1..6 = May..Oct 2026. Each month's requests are decided on the 6th of the
next month at 9 AM: after the owner's draw clears (the 5th), before subs are paid (the
10th). The kernel sees only facts known by then.
"""
from datetime import date, datetime, timedelta

import pipeline
from events import log as events
from seed.generate import PERIODS

START = date(2026, 5, 1)  # contracts, vendors and insurance are on file from day one

# when the GC learns each fact
KNOWN_AT = {
    "jobs": lambda r: START, "vendors": lambda r: START, "subcontracts": lambda r: START,
    "cois": lambda r: START, "change_orders": lambda r: r.date, "pay_requests": lambda r: r.submitted_at,
    "releases": lambda r: r.received_at, "lower_tier_notices": lambda r: r.notice_date,
    "ledger": lambda r: r.date, "emails": lambda r: r.sent_at, "site_evidence": lambda r: r.date,
}


def month_end(month: int) -> date:
    p = PERIODS[month - 1]
    return (p + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def in_month(d: date, month: int) -> bool:
    """Whether a period ending on `d` belongs to month 1..6: same calendar month, so an uploaded pay
    application dated mid-month counts too."""
    end = month_end(month)
    return (d.year, d.month) == (end.year, end.month)


def decision_time(month: int) -> datetime:
    d = month_end(month) + timedelta(days=6)
    return datetime(d.year, d.month, d.day, 9, 0)


def as_datetime(at) -> datetime:
    return at if isinstance(at, datetime) else datetime(at.year, at.month, at.day)


def load_seed(data: dict[str, list], path=events.DEFAULT_PATH) -> int:
    """Append every seed row as a fact, in the order the GC would have learned it."""
    facts = sorted(((as_datetime(KNOWN_AT[t](row)), t, row) for t in events.TABLES for row in data.get(t, [])),
                   key=lambda f: f[0])
    return events.append_facts(facts, path=path)


def paid_requests(state: dict[str, list], path=events.DEFAULT_PATH) -> set[str]:
    """Requests with a payment: this log's own receipts, or a sub payment in the bank ledger."""
    paid = {e.request_id for e in events.read(kind="execution", path=path) if e.data.get("status", "paid") == "paid"}
    return paid | {e.request_id for e in state["ledger"] if e.kind.value == "sub_payment" and e.request_id}


def replay(through: int, write: bool = True, decide=None, path=events.DEFAULT_PATH, judge=None,
           redo: bool = False) -> list:
    """Decide every request for months 1..through that has no decision yet.

    write=False is the what-if mode: decisions come back but nothing is appended,
    e.g. to rerun October under a changed contract. With `judge` (e.g. judgment.judge) the
    model answers first, its Proposal reaches the kernel, and a `judgment` event is logged
    before each kernel trace. redo=True decides every unpaid request again (appended; the newest
    decision is the one that counts), e.g. to put Jev in the loop on decisions made without it.

    A request is decided on its month's decision day, on what was known by then; one that arrived
    after that day (a late upload) is decided as of its arrival.
    """
    decide = decide or pipeline.decide
    everything = events.state(path=path)
    if not write:
        done = set()
    elif redo:
        done = paid_requests(everything, path)
    else:
        done = {e.request_id for e in events.read(kind="kernel_trace", path=path)}
    out = []
    for month in range(1, through + 1):
        due: dict[datetime, list[str]] = {}
        for req in everything["pay_requests"]:
            if in_month(req.period_end, month) and req.id not in done:
                due.setdefault(max(decision_time(month), as_datetime(req.submitted_at)), []).append(req.id)
        for at in sorted(due):
            state = events.state(as_of=at, path=path)
            known = {r.id for r in state["pay_requests"]}
            for rid in due[at]:
                if rid not in known:
                    continue
                if judge is not None:
                    d, result = pipeline.judge_and_decide(state, rid, judge=judge)
                    if write:
                        events.append("judgment", pipeline.judgment_event(result), request_id=rid, at=at, path=path)
                else:
                    d = decide(state, rid)
                if write:
                    events.append("kernel_trace", d, request_id=rid, at=at, path=path)
                out.append(d)
    return out


def ledger_key(e) -> tuple:
    """What makes two ledger rows the same money movement, whichever system recorded them."""
    if e.kind.value == "owner_draw":
        return ("owner_draw", e.job_id, e.period_end)
    return ("sub_payment", e.request_id or e.id)


def double_payments(entries) -> dict[str, int]:
    """Requests the bank shows paid more than once (sync keeps one row per request; this says so out loud)."""
    counts: dict[str, int] = {}
    for e in entries:
        if e.kind.value == "sub_payment" and e.request_id:
            counts[e.request_id] = counts.get(e.request_id, 0) + 1
    return {rid: n for rid, n in counts.items() if n > 1}


def sync_ledger(entries, path=events.DEFAULT_PATH) -> int:
    """Append bank ledger entries (e.g. from Nessie) the log doesn't already have, each at its own date."""
    known = {ledger_key(e) for e in events.state(path=path)["ledger"]}
    added = 0
    for e in sorted(entries, key=lambda e: e.date):
        if ledger_key(e) not in known:
            events.append("fact", e, table="ledger", at=as_datetime(e.date), path=path)
            known.add(ledger_key(e))
            added += 1
    return added
