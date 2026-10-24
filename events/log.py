"""Append-only event log in SQLite. Owner: B.

Every fact, judgment, decision, signature and execution is one row. Nothing is ever
updated or deleted (SQLite triggers refuse it). State at any moment is a fold over the
facts up to that moment: a later fact with the same id supersedes the earlier one.
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

KINDS = {"fact", "judgment", "decision", "signature", "execution"}

# state table -> model. Labels are ground truth and never enter the log.
TABLES = {
    "jobs": Job, "vendors": Vendor, "subcontracts": Subcontract, "change_orders": ChangeOrder,
    "pay_requests": PayRequest, "releases": Release, "lower_tier_notices": LowerTierNotice,
    "cois": COI, "ledger": LedgerEntry, "emails": Email, "site_evidence": SiteEvidence,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    tbl TEXT,
    data TEXT NOT NULL
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
    return at.isoformat(timespec="seconds")  # fixed width, so string order is time order


class EventLog:
    """Opens a connection per call, so it is safe to share across API threads."""

    def __init__(self, path: Path | str = "data/events.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")  # readers (SSE) don't block the writer
            db.executescript(SCHEMA)

    def _db(self):
        return sqlite3.connect(self.path)

    def append(self, kind: str, data: BaseModel | dict, at: date | datetime, table: str | None = None) -> int:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        if (kind == "fact") != (table in TABLES):
            raise ValueError("facts need a state table; other kinds must not have one")
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        with self._db() as db:
            cur = db.execute("INSERT INTO events (at, kind, tbl, data) VALUES (?, ?, ?, ?)",
                             (_ts(at), kind, table, json.dumps(payload)))
            return cur.lastrowid

    def read(self, after: int = 0, kind: str | None = None, as_of: date | datetime | None = None) -> list[Event]:
        """Events in write order. `after` is a seq (for SSE tails); `as_of` caps business time."""
        sql, args = "SELECT seq, at, kind, tbl, data FROM events WHERE seq > ?", [after]
        if kind:
            sql, args = sql + " AND kind = ?", args + [kind]
        if as_of:
            sql, args = sql + " AND at <= ?", args + [_ts(as_of)]
        with self._db() as db:
            rows = db.execute(sql + " ORDER BY seq", args).fetchall()
        return [Event(seq=s, at=datetime.fromisoformat(a), kind=k, table=t, data=json.loads(d))
                for s, a, k, t, d in rows]

    def state(self, as_of: date | datetime | None = None) -> dict[str, list]:
        """The world as it was known at `as_of` (default: everything), same shape as seed.generate()."""
        latest = {t: {} for t in TABLES}
        for e in self.read(kind="fact", as_of=as_of):
            row = TABLES[e.table].model_validate(e.data)
            latest[e.table][row.id] = row  # dicts keep first-insert order; a correction replaces in place
        return {t: list(rows.values()) for t, rows in latest.items()}
