"""The Postgres log behaves exactly like the SQLite one. Runs only with TEST_DATABASE_URL set
(a throwaway database; the table is dropped first), e.g.:

    docker run -d --rm -e POSTGRES_PASSWORD=test -p 54329:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:54329/postgres pytest tests/test_events_postgres.py
"""
import os
from datetime import datetime

import pytest

from events import log as events
from events.replay import load_seed, replay
from seed.anomalies import with_demo
from seed.generate import generate
from shared.schema import Action

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
DATA, _ = with_demo(generate())


@pytest.fixture
def pg(monkeypatch):
    import psycopg
    with psycopg.connect(URL, autocommit=True) as db:  # fresh table, triggers and all; archives from earlier runs too
        old = [r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE tablename LIKE 'events_archive_%'")]
        for table in ["events", *old]:
            db.execute(f"DROP TABLE IF EXISTS {table}")
    events._pg_ready.clear()
    monkeypatch.setenv("DATABASE_URL", URL)
    return URL


def test_round_trip_and_replay(pg):
    assert events.is_empty() and events.where() == "the Postgres log (DATABASE_URL)"
    assert load_seed(DATA) == 419 and not events.is_empty()
    state = events.state()
    for table, rows in state.items():
        assert {r.id: r for r in rows} == {r.id: r for r in DATA[table]}, table
    decided = replay(6)
    assert len(decided) == 90 and sorted(d.request_id for d in decided if d.action == Action.hold) == \
        ["pr_sub_01_6", "pr_sub_03_6", "pr_sub_04_6"]
    assert replay(6) == []  # idempotent, like SQLite
    seqs = [e.seq for e in events.read()]
    assert seqs == sorted(seqs) and len(seqs) == 419 + 90


def test_append_only_in_postgres(pg):
    import psycopg
    seq = events.append("signature", {"sig": "ab", "nonce": "n1"}, request_id="pr_1")
    for sql in ["UPDATE events SET kind = 'fact'", "DELETE FROM events", "TRUNCATE events"]:
        with psycopg.connect(URL) as db, pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            db.execute(sql)
    [e] = events.read_for_request("pr_1")
    assert e.seq == seq and e.data["nonce"] == "n1"


def test_as_of_and_tail(pg):
    load_seed(DATA)
    july = events.state(as_of=datetime(2026, 8, 6, 9))
    assert max(r.period_end for r in july["pay_requests"]).month == 7
    last = events.read()[-1].seq
    events.append("execution", {"request_id": "x"}, request_id="x")
    assert [e.kind for e in events.read(after=last)] == ["execution"]


def test_explicit_path_still_uses_sqlite(pg, tmp_path):
    p = tmp_path / "e.db"
    events.append("signature", {"sig": "s"}, request_id="only-sqlite", path=p)
    assert events.read_for_request("only-sqlite") == []  # not in Postgres
    assert len(events.read_for_request("only-sqlite", path=p)) == 1


def test_advisory_lock_serializes_connections(pg, monkeypatch):
    """Two Vercel instances = two connections; the payments lock lets one in at a time."""
    import threading
    import time
    from events import log as events
    order = []

    def worker(name):
        with events.exclusive("payments"):
            order.append(f"{name} in")
            time.sleep(0.5)
            order.append(f"{name} out")

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert order in (["a in", "a out", "b in", "b out"], ["b in", "b out", "a in", "a out"])


def test_archive_sets_the_table_aside_and_a_running_api_keeps_working(pg):
    import psycopg
    load_seed(DATA)
    replay(6)
    n = len(events.read())
    moved = events.archive()
    table = moved.removeprefix("Postgres table ")
    assert events.is_empty()
    with psycopg.connect(URL) as db:
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == n
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):  # the archive stays read-only
            db.execute(f"DELETE FROM {table}")
    # this process had the old table ready: appending now goes to the fresh one, numbered from 1
    assert events.append("signature", {"sig": "s"}, request_id="after") == 1
    with psycopg.connect(URL) as db, pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute("UPDATE events SET kind = 'fact'")
    assert load_seed(DATA) == 419  # the demo company loads again into the fresh log


def test_snapshot_copies_postgres_into_sqlite(pg, tmp_path):
    load_seed(DATA)
    replay(6)
    out = tmp_path / "offline.db"
    assert events.copy_to_sqlite(out) == 419 + 90
    assert [e.model_dump() for e in events.read(path=out)] == [e.model_dump() for e in events.read()]
