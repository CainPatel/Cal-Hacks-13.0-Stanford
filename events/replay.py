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


def decision_time(month: int) -> datetime:
    d = month_end(month) + timedelta(days=6)
    return datetime(d.year, d.month, d.day, 9, 0)


def as_datetime(at) -> datetime:
    return at if isinstance(at, datetime) else datetime(at.year, at.month, at.day)


def load_seed(data: dict[str, list], path=events.DEFAULT_PATH) -> int:
    """Append every seed row as a fact, in the order the GC would have learned it."""
    facts = sorted(((as_datetime(KNOWN_AT[t](row)), t, row) for t in events.TABLES for row in data.get(t, [])),
                   key=lambda f: f[0])
    for at, table, row in facts:
        events.append("fact", row, at=at, table=table, path=path)
    return len(facts)


def replay(through: int, write: bool = True, decide=None, path=events.DEFAULT_PATH, judge=None) -> list:
    """Decide every request for months 1..through that has no decision yet.

    write=False is the what-if mode: decisions come back but nothing is appended,
    e.g. to rerun October under a changed contract. With `judge` (e.g. judgment.judge) the
    model answers first, its Proposal reaches the kernel, and a `judgment` event is logged
    before each kernel trace.
    """
    decide = decide or pipeline.decide
    done = {e.request_id for e in events.read(kind="kernel_trace", path=path)} if write else set()
    out = []
    for month in range(1, through + 1):
        at = decision_time(month)
        state = events.state(as_of=at, path=path)
        for req in state["pay_requests"]:
            if req.period_end != month_end(month) or req.id in done:
                continue
            if judge is not None:
                d, result = pipeline.judge_and_decide(state, req.id, judge=judge)
                if write:
                    events.append("judgment", pipeline.judgment_event(result), request_id=req.id, at=at, path=path)
            else:
                d = decide(state, req.id)
            if write:
                events.append("kernel_trace", d, request_id=req.id, at=at, path=path)
            out.append(d)
    return out
