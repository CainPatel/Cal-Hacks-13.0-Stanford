"""Append-only event log in SQLite. Owner: B.

Every fact, judgment, kernel trace, signature and execution is one row. Nothing is ever
updated or deleted (SQLite triggers refuse it). State at any moment is a fold over the
facts up to that moment: a later fact with the same id supersedes the earlier one.

Two clocks: `ts` is when the row was written (wall clock); `at` is business time, the
moment the fact or decision belongs to (defaults to `ts`). Replay filters on `at`.

    from events import log as events
    events.append("signature", {...}, request_id=req.id)        # what kernel/executor.py does
"""
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from shared.schema import (
    COI, ChangeOrder, Email, Event, Job, LedgerEntry, LowerTierNotice, PayRequest, Release,
    SiteEvidence, Subcontract, Vendor,
)

DEFAULT_PATH = Path("data/events.db")
KINDS = {"fact", "judgment", "kernel_trace", "signature", "execution"}

# state table -> model. Labels are ground truth and never enter the log.
TABLES = {
    "jobs": Job, "vendors": Vendor, "subcontracts": Subcontract, "change_orders": ChangeOrder,
    "pay_requests": PayRequest, "releases": Release, "lower_tier_notices": LowerTierNotice,
    "cois": COI, "ledger": LedgerEntry, "emails": Email, "site_evidence": SiteEvidence,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    request_id TEXT,
    tbl TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""


def _ts(at: date | datetime) -> str:
    if not isinstance(at, datetime):
        at = datetime(at.year, at.month, at.day)
    return at.replace(tzinfo=None).isoformat(timespec="seconds")  # fixed width: string order is time order


def _connect(path: Path) -> sqlite3.Connection:
    """A connection per call, so the log is safe to share across API threads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")  # readers (SSE) don't block the writer
    db.executescript(SCHEMA)
    return db


def append(kind: str, payload: BaseModel | dict, request_id: str | None = None, path: Path = DEFAULT_PATH,
           at: date | datetime | None = None, table: str | None = None) -> int:
    """Append one row and return its seq. Facts need `table` (a key of TABLES); nothing else may have one."""
    if kind not in KINDS:
        raise ValueError(f"unknown event kind {kind!r}")
    if (kind == "fact") != (table in TABLES):
        raise ValueError("facts need a state table; other kinds must not have one")
    data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    now = _ts(datetime.now())
    with _connect(path) as db:
        cur = db.execute(
            "INSERT INTO events (ts, at, kind, request_id, tbl, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (now, _ts(at) if at else now, kind, request_id, table, json.dumps(data)))
        return cur.lastrowid


def read(after: int = 0, kind: str | None = None, as_of: date | datetime | None = None,
         request_id: str | None = None, path: Path = DEFAULT_PATH) -> list[Event]:
    """Events in write order. `after` is a seq (for SSE tails); `as_of` caps business time."""
    sql, args = "SELECT seq, ts, at, kind, request_id, tbl, payload FROM events WHERE seq > ?", [after]
    for col, val in (("kind", kind), ("request_id", request_id)):
        if val:
            sql, args = sql + f" AND {col} = ?", args + [val]
    if as_of:
        sql, args = sql + " AND at <= ?", args + [_ts(as_of)]
    with _connect(path) as db:
        rows = db.execute(sql + " ORDER BY seq", args).fetchall()
    return [Event(seq=s, ts=datetime.fromisoformat(t), at=datetime.fromisoformat(a), kind=k,
                  request_id=r, table=tb, data=json.loads(d)) for s, t, a, k, r, tb, d in rows]


def read_for_request(request_id: str, path: Path = DEFAULT_PATH) -> list[Event]:
    return read(request_id=request_id, path=path)


def state(as_of: date | datetime | None = None, path: Path = DEFAULT_PATH) -> dict[str, list]:
    """The world as it was known at `as_of` (default: everything), same shape as seed.generate()."""
    latest = {t: {} for t in TABLES}
    for e in read(kind="fact", as_of=as_of, path=path):
        row = TABLES[e.table].model_validate(e.data)
        latest[e.table][row.id] = row  # dicts keep first-insert order; a correction replaces in place
    return {t: list(rows.values()) for t, rows in latest.items()}
