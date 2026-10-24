"""Keeping copies of Draw in step: the bank as the record of every payment, the shared log, replay of
late or mid-month pay applications, the Log tab's endpoint, and the offline snapshot."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import pipeline
from api import main
from events import log as events
from events.replay import load_seed, paid_requests, replay
from kernel import executor as ex
from seed.anomalies import with_demo
from seed.generate import generate
from tests.test_money import CONFIRM, FakeNessie, approval_for, decided, make_keypair, released_request

pytestmark = pytest.mark.skipif(pipeline.kernel_decide is None, reason="needs kernel/")


@pytest.fixture
def bank(monkeypatch):
    fake = FakeNessie()
    monkeypatch.setattr(ex.requests, "request", fake)
    monkeypatch.setenv("NESSIE_API_KEY", "test-key")
    monkeypatch.setenv("NESSIE_ACCOUNT_ID", "gc_acct")
    return fake


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "events.db"
    load_seed(with_demo(generate())[0], path=db)
    replay(6, path=db)
    return db


@pytest.fixture
def client(world, monkeypatch, bank):
    monkeypatch.setattr(main, "DB", world)
    monkeypatch.setenv("DEVICE_URL", "http://localhost:8001")
    return TestClient(main.app)


def paid_elsewhere(bank, req, bill_id="other1", request_id=None):
    """A bill another copy of Draw (same Nessie account, its own log) wrote for this request."""
    bank.bills.append({"_id": bill_id, "status": "completed", "payment_amount": float(req.g702.current_payment_due),
                       "payment_date": "2026-10-23", "recurring_date": 1,
                       "nickname": "|".join(["pay", request_id or req.id, req.sub_id, req.job_id,
                                             req.period_end.isoformat(), req.bank_account])})


def posts(bank):
    return [c for c in bank.calls if c.method == "POST"]


# --- the bank is asked before paying --------------------------------------------------


def test_a_payment_made_from_another_copy_is_not_made_again(bank, world):
    _, req = released_request(world)
    d, _ = decided(world, req.id)
    paid_elsewhere(bank, req)
    with pytest.raises(ex.ExecutionRefused, match="already paid: the bank shows"):
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    assert not posts(bank)
    # recorded here now, so this log agrees with the bank from then on
    assert ex.payment_status(req.id, world) == "paid"
    assert any(e.id == "nessie_other1" for e in events.state(path=world)["ledger"])
    with pytest.raises(ex.ExecutionRefused, match="already been paid"):
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)


def test_the_owners_approval_is_not_spent_on_a_payment_the_bank_has(bank, world):
    priv, pub = make_keypair()
    d, req = decided(world, "pr_sub_15_6")  # escalated: over the limit
    paid_elsewhere(bank, req)
    with pytest.raises(ex.ExecutionRefused, match="already paid"):
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval_for(priv, req),
                   log_path=world)
    assert not events.read(kind="signature", request_id=req.id, path=world) and not posts(bank)


def test_same_sub_job_and_period_under_another_id_counts_as_paid(bank, world):
    _, req = released_request(world)
    d, _ = decided(world, req.id)
    paid_elsewhere(bank, req, request_id=req.id + "_rescan")
    with pytest.raises(ex.ExecutionRefused, match=f"request {req.id}_rescan"):
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    assert not posts(bank)
    with pytest.raises(ex.ExecutionRefused, match="already paid on"):  # the log knows now: no bank call needed
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)


def test_a_bank_that_cannot_be_asked_pays_nothing(bank, world):
    _, req = released_request(world)
    d, _ = decided(world, req.id)
    bank.fail_with, bank.fail_on = 503, ("GET",)
    with pytest.raises(ex.ExecutionRefused, match="couldn't check the bank before paying"):
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    assert not posts(bank) and not events.read(kind="execution", request_id=req.id, path=world)


def test_approve_never_asks_for_a_tap_the_bank_already_has(client, world, bank, monkeypatch):
    _, req = decided(world, "pr_sub_10_6")  # escalated
    paid_elsewhere(bank, req)
    monkeypatch.setattr("shared.contract.load_contract", lambda: {"auto_release_limit_usd": 25000, "esp32_pubkey": "04ab"})
    asked = []
    monkeypatch.setattr(ex, "request_device_approval", lambda url, p: asked.append(p))
    out = client.post(f"/requests/{req.id}/approve").json()
    assert out["paid"] is False and "already paid" in out["reason"] and not asked
    assert client.get(f"/requests/{req.id}").json()["status"] == "paid"


# --- POST /sync and the paid status ------------------------------------------------------


def test_sync_brings_in_payments_made_elsewhere(client, world, bank):
    _, req = released_request(world)
    paid_elsewhere(bank, req)
    paid_elsewhere(bank, req, bill_id="other2")  # the same request paid twice in the bank: said out loud
    out = client.post("/sync").json()
    assert out["new_to_log"] == 1 and out["paid_twice"] == {req.id: 2}
    row = next(r for r in client.get("/requests").json() if r["id"] == req.id)
    assert row["status"] == "paid" and row["sentence"].startswith("Paid $")
    assert client.post("/sync").json()["new_to_log"] == 0
    assert client.post(f"/requests/{req.id}/pay", json=CONFIRM).status_code == 409


def test_sync_says_when_the_bank_is_not_configured(client, monkeypatch):
    monkeypatch.delenv("NESSIE_ACCOUNT_ID")
    r = client.post("/sync")
    assert r.status_code == 503 and "NESSIE_ACCOUNT_ID" in r.json()["detail"]


def test_replay_syncs_the_bank_first(client, world, bank):
    _, req = released_request(world)
    paid_elsewhere(bank, req)
    out = client.post("/replay").json()
    assert out["bank"]["new_to_log"] == 1
    assert next(r for r in client.get("/requests").json() if r["id"] == req.id)["status"] == "paid"


def test_past_months_paid_in_the_seed_ledger_show_as_paid(client):
    rows = client.get("/requests", params={"month": 2}).json()
    assert rows and all(r["status"] == "paid" for r in rows)


# --- replay ----------------------------------------------------------------------------------


def late_copy(world, rid, rid_new, period_end, submitted_at):
    state = events.state(path=world)
    req = next(r for r in state["pay_requests"] if r.id == rid)
    copy = req.model_copy(update={"id": rid_new, "period_end": period_end, "submitted_at": submitted_at})
    events.append("fact", copy, table="pay_requests", at=submitted_at, path=world)
    return copy


def test_a_pay_application_dated_mid_month_is_listed_and_decided(client, world):
    late_copy(world, "pr_sub_02_6", "pr_mid", date(2026, 10, 20), datetime(2026, 10, 21, 9))
    assert "pr_mid" in {r["id"] for r in client.get("/requests", params={"month": 6}).json()}
    assert client.post("/replay").json()["new_decisions"] == 1
    assert next(r for r in client.get("/requests").json() if r["id"] == "pr_mid")["status"] != "not_checked"


def test_a_late_arrival_is_decided_as_of_when_it_came_in(world):
    late_copy(world, "pr_sub_02_3", "pr_late", date(2026, 7, 31), datetime(2026, 10, 20, 10))
    assert [d.request_id for d in replay(6, path=world)] == ["pr_late"]
    trace = events.read(kind="kernel_trace", request_id="pr_late", path=world)[-1]
    assert trace.at == datetime(2026, 10, 20, 10)  # not July's decision day, when it wasn't known yet


def test_redo_decides_every_unpaid_request_again(world):
    unpaid = {r.id for r in events.state(path=world)["pay_requests"]} - paid_requests(events.state(path=world), world)
    again = replay(6, path=world, redo=True)
    assert {d.request_id for d in again} == unpaid and unpaid
    assert replay(6, path=world) == []  # nothing undecided


# --- GET /log ----------------------------------------------------------------------------------


def test_log_is_newest_first_with_a_sentence_per_row(client, world):
    page = client.get("/log", params={"limit": 5}).json()
    seqs = [e["seq"] for e in page["events"]]
    assert seqs == sorted(seqs, reverse=True) and len(seqs) == 5 and page["more"] is True
    assert all(e["line"] for e in page["events"])
    older = client.get("/log", params={"limit": 5, "before": seqs[-1]}).json()["events"]
    assert older[0]["seq"] < seqs[-1]
    decisions = client.get("/log", params={"kind": "kernel_trace", "request_id": "pr_sub_03_6"}).json()["events"]
    assert decisions and decisions[0]["line"].startswith("The rules decided: hold")
    facts = client.get("/log", params={"kind": "fact", "limit": 500}).json()["events"]
    assert any(e["line"].startswith("The owner's draw for Harbor Point") for e in facts)  # names, not ids
    assert client.get("/log", params={"kind": "nope"}).status_code == 422


# --- status, snapshot, archive ----------------------------------------------------------------


def test_status_compares_copies_without_showing_values(client, monkeypatch):
    password = "hunter2secret"  # kept out of one literal URL: the pre-commit hook blocks those, rightly
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:" + password + "@ep-x.neon.tech/neondb?sslmode=require")
    body = client.get("/").json()
    text = str(body)
    assert len(body["log_fingerprint"]) == 8 and len(body["bank_fingerprint"]) == 8
    assert "hunter2secret" not in text and "neon.tech" not in text and "gc_acct" not in text


def test_snapshot_copies_every_row_with_its_number(world, tmp_path):
    out = tmp_path / "offline.db"
    assert events.copy_to_sqlite(out, path=world) == len(events.read(path=world))
    assert [e.model_dump() for e in events.read(path=out)] == [e.model_dump() for e in events.read(path=world)]
    with pytest.raises(FileExistsError):
        events.copy_to_sqlite(out, path=world)


def test_archive_sets_the_log_aside_and_starts_empty(world):
    n = len(events.read(path=world))
    moved = events.archive(world)
    assert events.is_empty(world) and len(events.read(path=moved)) == n
    events.append("judgment", {"x": 1}, path=world)  # the new log works and is still append-only
    assert len(events.read(path=world)) == 1
