"""The log is append-only, folds back to the seed exactly, and replay only sees the past."""
import sqlite3
from datetime import datetime

import pytest

from events import log as events
from events.replay import decision_time, load_seed, month_end, replay
from seed.anomalies import with_demo
from seed.generate import generate
from shared.schema import Action, Decision

DATA, _ = with_demo(generate())


def hold(state, rid):
    return Decision(request_id=rid, action=Action.hold, results=[])


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "events.db"
    load_seed(DATA, path=p)
    return p


def by_id(rows):
    return {r.id: r for r in rows}


def test_append_only(tmp_path):
    p = tmp_path / "e.db"
    seq = events.append("signature", {"sig": "ab"}, request_id="pr_1", path=p)  # the executor's call shape
    for sql in ["UPDATE events SET kind = 'fact'", "DELETE FROM events"]:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"), sqlite3.connect(p) as db:
            db.execute(sql)
    [e] = events.read_for_request("pr_1", path=p)
    assert e.seq == seq and e.kind == "signature" and e.at == e.ts


def test_rejects_bad_events(tmp_path):
    p = tmp_path / "e.db"
    with pytest.raises(ValueError):
        events.append("gossip", {}, path=p)
    with pytest.raises(ValueError):
        events.append("fact", {}, path=p)  # fact without a table
    with pytest.raises(ValueError):
        events.append("kernel_trace", {}, table="jobs", path=p)


def test_state_folds_back_to_the_seed(path):
    state = events.state(path=path)
    for table, rows in state.items():
        assert by_id(rows) == by_id(DATA[table]), table
    assert "labels" not in state  # ground truth never enters the log


def test_correction_supersedes(path):
    job = DATA["jobs"][0]
    events.append("fact", job.model_copy(update={"name": "Renamed"}), at=datetime(2026, 11, 1), table="jobs", path=path)
    assert by_id(events.state(path=path)["jobs"])[job.id].name == "Renamed"
    assert by_id(events.state(as_of=datetime(2026, 10, 31), path=path)["jobs"])[job.id].name == job.name


def test_as_of_hides_the_future(path):
    state = events.state(as_of=decision_time(3), path=path)  # July requests, decided Aug 6
    assert max(r.period_end for r in state["pay_requests"]) == month_end(3)
    paid = {e.request_id for e in state["ledger"] if e.request_id}
    assert all(r.id not in paid for r in state["pay_requests"] if r.period_end == month_end(3))
    assert any(e.kind == "owner_draw" and e.period_end == month_end(3) for e in state["ledger"])


def test_replay_decides_each_request_once_seeing_only_the_past(path):
    seen = []

    def decide(state, rid):
        seen.append(max(r.period_end for r in state["pay_requests"]))
        return hold(state, rid)

    assert len(replay(3, decide=decide, path=path)) == 15 * 3
    assert seen == [month_end(m) for m in (1, 2, 3) for _ in range(15)]
    assert replay(3, decide=decide, path=path) == []  # idempotent
    traces = events.read(kind="kernel_trace", path=path)
    assert len(traces) == 45 and all(e.request_id == e.data["request_id"] for e in traces)


def test_what_if_writes_nothing(path):
    assert len(replay(6, write=False, decide=hold, path=path)) == 15 * 6
    assert events.read(kind="kernel_trace", path=path) == []


def test_demo_history_is_clean_and_october_holds_the_story(path):
    """Replayed through the real kernel, May-Sep are never held; the demo holds land in October only."""
    pytest.importorskip("kernel.runner")
    held = {d.request_id for d in replay(6, path=path) if d.action == Action.hold}
    assert held and all(rid.endswith("_6") for rid in held), held
    assert "pr_sub_01_6" in held  # Coastal Concrete's supplier notice
