"""Append-only SQLite event log. Every fact, judgment, kernel trace, signature, and
execution gets a row; nothing is ever updated or deleted. A month replays by reading
rows back in order. Owner: A.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path("data/events.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,        -- fact | judgment | kernel_trace | signature | execution
    request_id TEXT,
    payload TEXT NOT NULL      -- JSON
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(SCHEMA)
    return db


def append(kind: str, payload: dict, request_id: str | None = None, path: Path = DEFAULT_PATH) -> int:
    """Appends one row and returns its sequence number. Never call UPDATE or DELETE on this table."""
    with _connect(path) as db:
        cur = db.execute(
            "INSERT INTO events (ts, kind, request_id, payload) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), kind, request_id, json.dumps(payload)),
        )
        return cur.lastrowid


def read_all(path: Path = DEFAULT_PATH) -> list[dict]:
    with _connect(path) as db:
        rows = db.execute("SELECT seq, ts, kind, request_id, payload FROM events ORDER BY seq").fetchall()
    return [
        {"seq": seq, "ts": ts, "kind": kind, "request_id": request_id, "payload": json.loads(payload)}
        for seq, ts, kind, request_id, payload in rows
    ]


def read_for_request(request_id: str, path: Path = DEFAULT_PATH) -> list[dict]:
    return [e for e in read_all(path) if e["request_id"] == request_id]
