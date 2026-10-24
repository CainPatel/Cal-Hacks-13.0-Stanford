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
import os
import sqlite3
from contextlib import contextmanager
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


# Deployed (Vercel), the log is Neon Postgres: same table, same append-only guarantee, enforced
# by triggers. Only the main log goes there; any explicit path (tests, the fuzzer) stays SQLite.
PG_SCHEMA = """
SELECT pg_advisory_xact_lock(4242);
CREATE TABLE IF NOT EXISTS events (
    seq BIGSERIAL PRIMARY KEY,
    ts TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    request_id TEXT,
    tbl TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger LANGUAGE plpgsql AS
    $$ BEGIN RAISE EXCEPTION 'events are append-only'; END $$;
CREATE OR REPLACE TRIGGER events_no_change BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_append_only();
CREATE OR REPLACE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_append_only();
"""
_pg_ready: set[str] = set()


def _postgres_url(path: Path) -> str | None:
    url = os.environ.get("DATABASE_URL")
    return url if url and Path(path) == DEFAULT_PATH else None


def _run(path: Path, sql: str, args=(), insert: bool = False):
    """One statement against the log: rows for a SELECT, the new seq for an insert."""
    url = _postgres_url(path)
    if url is None:
        with _connect(path) as db:
            cur = db.execute(sql, args)
            return cur.lastrowid if insert else cur.fetchall()
    import psycopg
    with psycopg.connect(url) as db:
        if url not in _pg_ready:
            db.execute(PG_SCHEMA)
            _pg_ready.add(url)
        cur = db.execute(sql.replace("?", "%s") + (" RETURNING seq" if insert else ""), args)
        return cur.fetchone()[0] if insert else cur.fetchall()


def is_empty(path: Path = DEFAULT_PATH) -> bool:
    if _postgres_url(path) is None and not Path(path).exists():
        return True
    return not _run(path, "SELECT 1 FROM events LIMIT 1")


def where(path: Path = DEFAULT_PATH) -> str:
    """Where the log lives, for messages (never prints credentials)."""
    return "the Postgres log (DATABASE_URL)" if _postgres_url(path) else str(path)


class LockTimeout(RuntimeError):
    """Another process held the lock for longer than the caller was willing to wait."""


@contextmanager
def exclusive(name: str, path: Path = DEFAULT_PATH, timeout_s: float = 150):
    """A lock shared by every process that writes this log, for check-then-act sequences (paying).

    Postgres: a session advisory lock, so every Vercel instance contends for the same lock; if an
    instance dies, its connection closes and the lock goes with it. SQLite: an OS file lock beside
    the database, which covers several local worker processes too.
    """
    url = _postgres_url(path)
    if url is not None:
        import psycopg
        with psycopg.connect(url, autocommit=True) as db:
            db.execute("SELECT set_config('lock_timeout', %s, false)", (f"{int(timeout_s * 1000)}ms",))
            try:
                db.execute("SELECT pg_advisory_lock(hashtext(%s))", (name,))
            except psycopg.errors.LockNotAvailable as e:
                raise LockTimeout(f"waited {timeout_s:.0f}s for the {name!r} lock") from e
            try:
                yield
            finally:
                db.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))
        return
    import fcntl
    import time
    lock_path = Path(path).with_name(Path(path).name + f".{name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as f:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise LockTimeout(f"waited {timeout_s:.0f}s for the {name!r} lock") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class LogNotConfigured(RuntimeError):
    """Deployed with no database: the SQLite fallback can't live on a read-only, throwaway disk."""


def _connect(path: Path) -> sqlite3.Connection:
    """A connection per call, so the log is safe to share across API threads."""
    path = Path(path)
    if os.environ.get("VERCEL") and path == DEFAULT_PATH:
        raise LogNotConfigured("The event log isn't connected: add Neon to this Vercel project "
                               "(Storage → Neon → Connect), redeploy, then load the demo data.")
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
    return _run(path, "INSERT INTO events (ts, at, kind, request_id, tbl, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (now, _ts(at) if at else now, kind, request_id, table, json.dumps(data)), insert=True)


def append_facts(facts: list[tuple[datetime, str, BaseModel]], path: Path = DEFAULT_PATH) -> int:
    """Append many facts (at, table, row) in one transaction: loading a seed into a remote
    Postgres one connection per row would take minutes."""
    now = _ts(datetime.now())
    rows = []
    for at, table, row in facts:
        if table not in TABLES:
            raise ValueError(f"unknown state table {table!r}")
        rows.append((now, _ts(at), "fact", None, table, json.dumps(row.model_dump(mode="json"))))
    sql = "INSERT INTO events (ts, at, kind, request_id, tbl, payload) VALUES (?, ?, ?, ?, ?, ?)"
    url = _postgres_url(path)
    if url is None:
        with _connect(path) as db:
            db.executemany(sql, rows)
        return len(rows)
    import psycopg
    with psycopg.connect(url) as db:
        if url not in _pg_ready:
            db.execute(PG_SCHEMA)
            _pg_ready.add(url)
        with db.cursor() as cur:
            cur.executemany(sql.replace("?", "%s"), rows)
    return len(rows)


def copy_to_sqlite(dst: Path, path: Path = DEFAULT_PATH) -> int:
    """Copy every row of the log (the Postgres log when DATABASE_URL is set) into a new SQLite file,
    seq and both clocks unchanged: an offline copy of the shared log, for when there's no network."""
    dst = Path(dst)
    if dst.exists():
        raise FileExistsError(f"{dst} already exists; move it aside first")
    rows = _run(path, "SELECT seq, ts, at, kind, request_id, tbl, payload FROM events ORDER BY seq")
    with _connect(dst) as db:
        db.executemany("INSERT INTO events (seq, ts, at, kind, request_id, tbl, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       rows)
    return len(rows)


def archive(path: Path = DEFAULT_PATH) -> str:
    """Set the whole log aside and start an empty one; nothing is deleted. Postgres renames the table
    (with its index and sequence) to events_archive_<time> and creates a fresh `events` in the same
    transaction, so a running API never sees a missing table. SQLite renames the files beside it.
    Returns where the old log went."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    url = _postgres_url(path)
    if url is None:
        path = Path(path)
        moved = path.with_name(f"archive-{stamp}-{path.name}")
        for suffix in ("", "-wal", "-shm"):
            if Path(f"{path}{suffix}").exists():
                Path(f"{path}{suffix}").rename(f"{moved}{suffix}")
        return str(moved)
    import psycopg
    old = f"events_archive_{stamp}"
    with psycopg.connect(url) as db:  # one transaction: commits on success, rolls back on any error
        db.execute("SELECT pg_advisory_xact_lock(4242)")
        db.execute(f"ALTER TABLE events RENAME TO {old}")
        db.execute(f"ALTER INDEX events_pkey RENAME TO {old}_pkey")
        db.execute(f"ALTER INDEX events_at RENAME TO {old}_at")
        db.execute(f"ALTER SEQUENCE events_seq_seq RENAME TO {old}_seq_seq")
        db.execute(PG_SCHEMA)
    _pg_ready.add(url)
    return f"Postgres table {old}"


def read(after: int = 0, kind: str | None = None, as_of: date | datetime | None = None,
         request_id: str | None = None, path: Path = DEFAULT_PATH) -> list[Event]:
    """Events in write order. `after` is a seq (for SSE tails); `as_of` caps business time."""
    sql, args = "SELECT seq, ts, at, kind, request_id, tbl, payload FROM events WHERE seq > ?", [after]
    for col, val in (("kind", kind), ("request_id", request_id)):
        if val:
            sql, args = sql + f" AND {col} = ?", args + [val]
    if as_of:
        sql, args = sql + " AND at <= ?", args + [_ts(as_of)]
    rows = _run(path, sql + " ORDER BY seq", args)
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
