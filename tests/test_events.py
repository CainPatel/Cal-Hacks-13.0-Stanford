"""The log is append-only, folds back to the seed exactly, and replay only sees the past."""
import sqlite3
from datetime import datetime

import pytest

from events.log import EventLog
from events.replay import decision_time, load_seed, month_end, replay
from seed.anomalies import with_demo
from seed.generate import generate
from shared.schema import Action, Decision

DATA, _ = with_demo(generate())


@pytest.fixture
def log(tmp_path):
    log = EventLog(tmp_path / "events.db")
    load_seed(log, DATA)
    return log


def by_id(rows):
    return {r.id: r for r in rows}


def test_append_only(tmp_path):
    log = EventLog(tmp_path / "e.db")
    seq = log.append("decision", {"request_id": "x"}, at=datetime(2026, 10, 1))
    for sql in ["UPDATE events SET kind = 'fact'", "DELETE FROM events"]:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"), sqlite3.connect(log.path) as db:
            db.execute(sql)
    assert [e.seq for e in log.read()] == [seq]


def test_rejects_bad_events(tmp_path):
    log = EventLog(tmp_path / "e.db")
    with pytest.raises(ValueError):
        log.append("gossip", {}, at=datetime(2026, 10, 1))
    with pytest.raises(ValueError):
        log.append("fact", {}, at=datetime(2026, 10, 1))  # fact without a table
    with pytest.raises(ValueError):
        log.append("decision", {}, at=datetime(2026, 10, 1), table="jobs")


def test_state_folds_back_to_the_seed(log):
    state = log.state()
    for table, rows in state.items():
        assert by_id(rows) == by_id(DATA[table]), table
    assert "labels" not in state  # ground truth never enters the log


def test_correction_supersedes(log):
    job = DATA["jobs"][0]
    log.append("fact", job.model_copy(update={"name": "Renamed"}), at=datetime(2026, 11, 1), table="jobs")
    assert by_id(log.state()["jobs"])[job.id].name == "Renamed"
    assert by_id(log.state(as_of=datetime(2026, 10, 31))["jobs"])[job.id].name == job.name


def test_as_of_hides_the_future(log):
    state = log.state(as_of=decision_time(3))  # July requests, decided Aug 6
    assert max(r.period_end for r in state["pay_requests"]) == month_end(3)
    paid = {e.request_id for e in state["ledger"] if e.request_id}
    assert all(r.id not in paid for r in state["pay_requests"] if r.period_end == month_end(3))
    assert any(e.kind == "owner_draw" and e.period_end == month_end(3) for e in state["ledger"])


def test_replay_decides_each_request_once_seeing_only_the_past(log):
    seen = []

    def decide(state, rid):
        seen.append(max(r.period_end for r in state["pay_requests"]))
        return Decision(request_id=rid, action=Action.hold, results=[])

    first = replay(log, 3, decide=decide)
    assert len(first) == 15 * 3
    assert seen == [month_end(m) for m in (1, 2, 3) for _ in range(15)]
    assert replay(log, 3, decide=decide) == []  # idempotent
    assert len(log.read(kind="decision")) == 45


def test_what_if_writes_nothing(log):
    out = replay(log, 6, write=False, decide=lambda s, rid: Decision(request_id=rid, action=Action.hold, results=[]))
    assert len(out) == 15 * 6 and log.read(kind="decision") == []
