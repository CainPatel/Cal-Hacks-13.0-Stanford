"""The money stage: the Nessie sandbox bank, the pay-once and approval-release rules, and POST /requests/{id}/pay."""
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

import pipeline
from api import main
from events import log as events
from events.replay import load_seed, replay, sync_ledger
from kernel import executor as ex
from seed.anomalies import with_demo
from seed.generate import generate
from shared.schema import Decision, LedgerEntry, LedgerKind
from tests.test_executor import make_keypair, sign

pytestmark = pytest.mark.skipif(pipeline.kernel_decide is None, reason="needs kernel/")


class FakeNessie:
    """Stands in for requests.request against the Nessie sandbox; records every call."""

    def __init__(self, fail_with=None, envelope=True):
        self.calls, self.fail_with, self.envelope, self.n = [], fail_with, envelope, 0
        self.deposits, self.bills = [], []

    def __call__(self, method, url, params=None, json=None, timeout=None):
        self.calls.append(SimpleNamespace(method=method, url=url, params=params, json=json))
        if self.fail_with is not None:
            if isinstance(self.fail_with, Exception):
                raise self.fail_with
            return SimpleNamespace(status_code=self.fail_with, text="bad request", json=lambda: {})
        self.n += 1
        oid = f"obj{self.n}"
        if method == "GET":
            body = self.deposits if url.endswith("/deposits") else self.bills if url.endswith("/bills") else []
        else:
            record = {**(json or {}), "_id": oid}
            if url.endswith("/deposits"):
                self.deposits.append(record)
            elif url.endswith("/bills"):
                self.bills.append(record)
            body = {"code": 201, "message": "Created", "objectCreated": record} if self.envelope else record
        return SimpleNamespace(status_code=201 if method == "POST" else 200, text="", json=lambda b=body: b)


@pytest.fixture
def bank(monkeypatch):
    fake = FakeNessie()
    monkeypatch.setattr(ex.requests, "request", fake)
    monkeypatch.setenv("NESSIE_API_KEY", "test-key")
    monkeypatch.setenv("NESSIE_ACCOUNT_ID", "gc_acct")
    monkeypatch.delenv("NESSIE_BASE_URL", raising=False)
    return fake


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "events.db"
    load_seed(with_demo(generate())[0], path=db)
    replay(6, path=db)
    return db


def decided(db, rid):
    state = events.state(path=db)
    req = next(r for r in state["pay_requests"] if r.id == rid)
    d = next(Decision.model_validate(e.data) for e in events.read(kind="kernel_trace", path=db) if e.request_id == rid)
    return d, req


def released_request(db):
    """An October (unpaid) request the kernel released outright."""
    for e in events.read(kind="kernel_trace", path=db):
        d = Decision.model_validate(e.data)
        if e.request_id.endswith("_6") and d.action.value == "release" and all(r.passed for r in d.results):
            return decided(db, e.request_id)


def approval_for(priv, req, nonce="n1"):
    import time
    payload = {"request_id": req.id, "payee": req.sub_id, "amount": str(req.g702.current_payment_due),
               "job": req.job_id, "nonce": nonce, "expires_at": int(time.time()) + 90}
    return ex.SignedApproval(payload, sign(priv, ex.canonical_payload_bytes(payload)), nonce)


# --- the bank call -----------------------------------------------------------------


def test_payment_is_a_completed_bill_on_the_gc_account(bank, world):
    d, req = released_request(world)
    receipt = ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    call = bank.calls[-1]
    assert (call.method, call.url) == ("POST", "https://prod-api.nessieisreal.com/accounts/gc_acct/bills")
    assert call.params == {"key": "test-key"}
    assert call.json["status"] == "completed" and call.json["payee"] == req.sub_id
    assert Decimal(str(call.json["payment_amount"])) == req.g702.current_payment_due
    assert receipt["status"] == "paid" and receipt["nessie_bill_id"] == "obj1"
    # the payment is in the ledger at once, so the next decision sees it (rule 01's "previously certified")
    paid = [e for e in events.state(path=world)["ledger"] if e.request_id == req.id and e.kind == LedgerKind.sub_payment]
    assert len(paid) == 1 and paid[0].amount == req.g702.current_payment_due and paid[0].id == "nessie_obj1"


def test_a_request_pays_only_once_even_without_a_signature(bank, world):
    d, req = released_request(world)
    ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    with pytest.raises(ex.ExecutionRefused, match="already been paid"):
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    assert len([c for c in bank.calls if c.method == "POST"]) == 1


def test_bank_refusal_releases_the_approval_for_a_retry(monkeypatch, bank, world):
    priv, pub = make_keypair()
    d, req = decided(world, "pr_sub_15_6")  # escalated: over the limit
    approval = approval_for(priv, req)
    bank.fail_with = 400
    with pytest.raises(ex.ExecutionRefused, match="didn't take the payment"):
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)
    bank.fail_with = None
    ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)
    assert [e.data["status"] for e in events.read(kind="execution", request_id=req.id, path=world)] == [
        "started", "failed", "started", "paid"]


@pytest.mark.parametrize("error", [requests.exceptions.ReadTimeout("slow"), requests.exceptions.ConnectionError("reset")])
def test_unknown_outcome_keeps_the_approval_spent(bank, world, error):
    priv, pub = make_keypair()
    d, req = decided(world, "pr_sub_15_6")
    approval = approval_for(priv, req)
    bank.fail_with = error
    with pytest.raises(ex.ExecutionRefused, match="outcome is unknown"):
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)
    bank.fail_with = None
    with pytest.raises(ex.ExecutionRefused, match="unknown outcome"):  # blocked until someone checks the bank
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)


def test_missing_bank_settings_never_spend_the_approval(monkeypatch, world):
    monkeypatch.delenv("NESSIE_API_KEY", raising=False)
    priv, pub = make_keypair()
    d, req = decided(world, "pr_sub_15_6")
    approval = approval_for(priv, req)
    with pytest.raises(ex.ExecutionRefused, match="NESSIE_API_KEY"):
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)
    failed = events.read(kind="execution", request_id=req.id, path=world)[-1].data
    assert failed["status"] == "failed" and failed["released_nonce"] == approval.nonce


# --- the GC's ledger in Nessie -----------------------------------------------------


def test_setup_writes_history_and_ledger_reads_it_back(bank):
    data = with_demo(generate())[0]
    account = ex.nessie_setup(data["ledger"])
    assert account == "obj2"  # customer obj1, then its checking account
    customer, acct = bank.calls[0], bank.calls[1]
    assert customer.url.endswith("/customers") and set(customer.json) == {"first_name", "last_name", "address"}
    assert acct.json == {"type": "Checking", "nickname": "Ridgeline Builders operating", "rewards": 0, "balance": 0}
    assert all(isinstance(d["amount"], int) for d in bank.deposits)  # Nessie deposits are whole dollars
    back, skipped = ex.nessie_ledger()
    assert skipped == []
    def key(e):
        return (e.kind, e.job_id, e.period_end, e.amount, e.sub_id, e.request_id, e.bank_account)
    assert sorted(map(key, back)) == sorted(map(key, data["ledger"]))  # exact amounts survive via the tag


def test_setup_without_an_id_in_the_response_finds_what_it_made(monkeypatch, bank):
    bank.envelope = False
    created = []

    def no_id(method, url, params=None, json=None, timeout=None):
        if method == "POST" and url.endswith("/customers"):
            created.append({**json, "_id": "cust9"})
            return SimpleNamespace(status_code=201, text="", json=lambda: {"message": "Customer created successfully"})
        if method == "GET" and url.endswith("/customers"):
            return SimpleNamespace(status_code=200, text="", json=lambda: created)
        return bank(method, url, params, json, timeout)

    monkeypatch.setattr(ex.requests, "request", no_id)
    assert ex.nessie_setup([]) == "obj1"


def test_ledger_ignores_entries_it_did_not_write(bank):
    bank.deposits.append({"_id": "x", "description": "paycheck", "amount": 5, "transaction_date": "2026-10-01"})
    bank.bills.append({"_id": "y", "nickname": "rent", "status": "completed", "payment_amount": 9})
    assert ex.nessie_ledger() == ([], [])


def test_sync_adds_only_what_the_log_lacks(world):
    state = events.state(path=world)
    draw = next(e for e in state["ledger"] if e.kind == LedgerKind.owner_draw)
    same_draw = draw.model_copy(update={"id": "nessie_dup"})
    new = LedgerEntry(id="nessie_new", job_id=draw.job_id, kind=LedgerKind.owner_draw, amount=Decimal("1"),
                      date=date(2026, 11, 5), period_end=date(2026, 11, 30))
    assert sync_ledger([same_draw, new], path=world) == 1
    assert sync_ledger([same_draw, new], path=world) == 0


# --- POST /requests/{id}/pay --------------------------------------------------------


CONFIRM = {"confirm": True}


@pytest.fixture
def client(world, monkeypatch, bank):
    monkeypatch.setattr(main, "DB", world)
    monkeypatch.setenv("DEVICE_URL", "http://localhost:8001")
    return TestClient(main.app)


def test_pay_a_released_request(client, world):
    _, req = released_request(world)
    r = client.post(f"/requests/{req.id}/pay", json=CONFIRM)
    assert r.status_code == 200 and r.json()["approved_on_device"] is False, r.text
    row = client.get(f"/requests/{req.id}").json()
    assert row["status"] == "paid" and row["sentence"].startswith("Paid $")
    assert client.post(f"/requests/{req.id}/pay", json=CONFIRM).status_code == 409  # already paid


def test_pay_refuses_a_hold(client):
    r = client.post("/requests/pr_sub_04_6/pay", json=CONFIRM)
    assert r.status_code == 409 and r.json()["detail"].startswith("On hold")


def test_escalation_goes_through_the_approval_key(client, world, monkeypatch):
    priv, pub = make_keypair()
    monkeypatch.setenv("APPROVAL_KEY_PUBKEY", pub)
    sent = {}

    def device(url, payload, timeout=None):
        sent.update(url=url, payload=payload)
        return ex.SignedApproval(payload, sign(priv, ex.canonical_payload_bytes(payload)), payload["nonce"])

    monkeypatch.setattr(ex, "request_device_approval", device)
    r = client.post("/requests/pr_sub_15_6/pay", json=CONFIRM)
    assert r.status_code == 200 and r.json()["approved_on_device"], r.text
    assert sent["url"] == "http://localhost:8001" and sent["payload"]["payee"] == "sub_15"


def test_escalation_without_a_registered_key(client, monkeypatch):
    monkeypatch.delenv("APPROVAL_KEY_PUBKEY", raising=False)
    monkeypatch.setattr("kernel.contract.load_contract", lambda: {"auto_release_limit_usd": 25000, "esp32_pubkey": ""})
    r = client.post("/requests/pr_sub_15_6/pay", json=CONFIRM)
    assert r.status_code == 503 and "isn't set up yet" in r.json()["detail"]


def test_rejection_on_the_device_pays_nothing(client, monkeypatch, bank):
    _, pub = make_keypair()
    monkeypatch.setenv("APPROVAL_KEY_PUBKEY", pub)
    monkeypatch.setattr(ex, "request_device_approval",
                        lambda url, payload, timeout=None: ex.SignedApproval(payload, "00", payload["nonce"], rejected=True))
    assert client.post("/requests/pr_sub_15_6/pay", json=CONFIRM).status_code == 403
    assert not [c for c in bank.calls if c.method == "POST"]


@pytest.mark.parametrize("error,code", [
    (requests.exceptions.ConnectionError("no route"), 502),
    (requests.exceptions.HTTPError(response=SimpleNamespace(status_code=408)), 504),
    (requests.exceptions.HTTPError(response=SimpleNamespace(status_code=409)), 409),
])
def test_device_problems_are_plain_errors(client, monkeypatch, error, code):
    _, pub = make_keypair()
    monkeypatch.setenv("APPROVAL_KEY_PUBKEY", pub)

    def device(url, payload, timeout=None):
        raise error

    monkeypatch.setattr(ex, "request_device_approval", device)
    assert client.post("/requests/pr_sub_15_6/pay", json=CONFIRM).status_code == code


def test_attack_console_without_a_key_says_so(monkeypatch):
    from fuzzer import nl_attack

    class NoKey:
        @property
        def beta(self):
            def parse(**kw):
                raise TypeError('"Could not resolve authentication method. Expected one of api_key"')
            return SimpleNamespace(messages=SimpleNamespace(parse=parse))

    with pytest.raises(nl_attack.AttackError, match="no Anthropic credentials"):
        nl_attack.plan("retainage is waived", client=NoKey())


def test_a_request_the_ledger_shows_paid_is_never_paid_again(bank, world):
    d, req = decided(world, "pr_sub_02_2")  # June: paid in the seed's bank history
    assert d.action.value == "release"
    with pytest.raises(ex.ExecutionRefused, match="bank ledger shows the payment"):
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    assert not bank.calls


# --- regressions from the money review ------------------------------------------------


def test_concurrent_pays_pay_once(client, world, bank):
    import threading
    _, req = released_request(world)
    results = []
    threads = [threading.Thread(target=lambda: results.append(client.post(f"/requests/{req.id}/pay", json=CONFIRM)))
               for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409, 409, 409]
    assert len([c for c in bank.calls if c.method == "POST"]) == 1


def test_unknown_outcome_blocks_every_retry_until_resolved(client, world, bank):
    _, req = released_request(world)
    bank.fail_with = requests.exceptions.ReadTimeout("slow")  # the bill may or may not exist
    assert client.post(f"/requests/{req.id}/pay", json=CONFIRM).status_code == 409
    bank.fail_with = None
    r = client.post(f"/requests/{req.id}/pay", json=CONFIRM)
    assert r.status_code == 409 and "unknown outcome" in r.json()["detail"]
    assert len([c for c in bank.calls if c.method == "POST"]) == 1  # only the attempt that timed out


def test_resolve_finds_the_bill_and_records_it(client, world, bank):
    _, req = released_request(world)
    bank.fail_with = requests.exceptions.ReadTimeout("slow")
    client.post(f"/requests/{req.id}/pay", json=CONFIRM)
    bank.fail_with = None
    bank.bills.append({"_id": "late1", "nickname": f"pay|{req.id}|{req.sub_id}|x|2026-10-31|acct",
                       "status": "completed", "payment_amount": float(req.g702.current_payment_due)})
    assert client.post(f"/requests/{req.id}/resolve").json()["outcome"] == "paid"
    assert client.get(f"/requests/{req.id}").json()["status"] == "paid"
    assert client.post(f"/requests/{req.id}/pay", json=CONFIRM).status_code == 409


def test_resolve_with_no_bill_allows_paying(client, world, bank):
    _, req = released_request(world)
    bank.fail_with = requests.exceptions.ReadTimeout("slow")
    client.post(f"/requests/{req.id}/pay", json=CONFIRM)
    bank.fail_with = None
    assert client.post(f"/requests/{req.id}/resolve").json()["outcome"] == "unpaid"
    assert client.post(f"/requests/{req.id}/pay", json=CONFIRM).status_code == 200


def test_gateway_5xx_is_not_proof_of_failure(bank, world):
    priv, pub = make_keypair()
    d, req = decided(world, "pr_sub_15_6")
    approval = approval_for(priv, req)
    bank.fail_with = 502
    with pytest.raises(ex.ExecutionRefused, match="outcome is unknown"):
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)
    bank.fail_with = None
    with pytest.raises(ex.ExecutionRefused, match="unknown outcome"):
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)


def test_same_application_under_another_id_is_not_paid_twice(client, world, bank):
    _, req = released_request(world)
    assert client.post(f"/requests/{req.id}/pay", json=CONFIRM).status_code == 200
    copy = req.model_copy(update={"id": req.id + "_rescan"})
    events.append("fact", copy, table="pay_requests", path=world)
    with pytest.raises(ex.ExecutionRefused, match="already paid on"):
        d, _ = decided(world, req.id)
        ex.execute(d.model_copy(update={"request_id": copy.id}), copy, auto_release_limit_usd=25000, log_path=world)
    assert len([c for c in bank.calls if c.method == "POST"]) == 1


def test_rule_12_flags_a_resubmitted_application():
    from kernel.rules.rule_12_not_a_duplicate import check
    data = with_demo(generate())[0]
    state, req = __import__("tests.judgment.conftest", fromlist=["state_for"]).state_for(data, "pr_sub_02_6")
    from kernel.state import KernelState
    copy = req.model_copy(update={"id": req.id + "_rescan"})
    ks = KernelState(job=state.job, subcontract=state.subcontract, vendor=state.vendor, all_requests=[copy])
    assert not check(ks, req).passed


def test_pay_decides_again_on_facts_that_arrived_later(client, world, bank):
    from datetime import date as d
    _, req = released_request(world)
    coi = next(c for c in events.state(path=world)["cois"] if c.sub_id == req.sub_id)
    events.append("fact", coi.model_copy(update={"wc_expiry": d(2026, 9, 1)}), table="cois", path=world)  # lapsed
    r = client.post(f"/requests/{req.id}/pay", json=CONFIRM)
    assert r.status_code == 409 and r.json()["detail"].startswith("On hold")
    assert not [c for c in bank.calls if c.method == "POST"]
    assert client.get(f"/requests/{req.id}").json()["status"] == "on_hold"  # the new decision is logged


def test_nessie_key_never_appears_in_errors(monkeypatch, bank, world):
    monkeypatch.setenv("NESSIE_API_KEY", "SECRETKEY123")
    bank.fail_with = requests.exceptions.ConnectionError(
        "Max retries exceeded with url: /accounts/gc_acct/bills?key=SECRETKEY123 (Caused by ...)")
    _, req = released_request(world)
    d, _ = decided(world, req.id)
    with pytest.raises(ex.ExecutionRefused) as err:
        ex.execute(d, req, auto_release_limit_usd=25000, log_path=world)
    assert "SECRETKEY123" not in str(err.value)
    assert all("SECRETKEY123" not in json.dumps(e.data) for e in events.read(path=world))


def test_signature_covers_the_bank_account(bank, world):
    priv, pub = make_keypair()
    d, req = decided(world, "pr_sub_15_6")
    import time
    payload = {"request_id": req.id, "payee": req.sub_id, "amount": str(req.g702.current_payment_due),
               "job": req.job_id, "bank_account": "111111111:2222222222", "nonce": "nb",
               "expires_at": int(time.time()) + 90}
    approval = ex.SignedApproval(payload, sign(priv, ex.canonical_payload_bytes(payload)), "nb")
    with pytest.raises(ex.ExecutionRefused, match="different bank account"):
        ex.execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub, approval=approval, log_path=world)


def test_pay_needs_a_json_confirmation(client, world):
    _, req = released_request(world)
    assert client.post(f"/requests/{req.id}/pay").status_code == 422  # a cross-site form post can't send JSON
    assert client.post(f"/requests/{req.id}/pay", json={"confirm": False}).status_code == 422


def test_ledger_rejects_a_tag_claiming_more_than_the_deposit(bank):
    bank.deposits.append({"_id": "d1", "description": "draw|job_1|2026-10-31|5000000", "amount": 1,
                          "transaction_date": "2026-11-05", "status": "completed"})
    bank.deposits.append({"_id": "d2", "description": "draw|job_1|not-a-date|5", "amount": 5,
                          "transaction_date": "2026-11-05"})
    entries, skipped = ex.nessie_ledger()
    assert entries == [] and len(skipped) == 2


def test_device_payload_includes_the_account(client, monkeypatch, world):
    priv, pub = make_keypair()
    monkeypatch.setenv("APPROVAL_KEY_PUBKEY", pub)
    sent = {}

    def device(url, payload, timeout=None):
        sent.update(payload)
        return ex.SignedApproval(payload, sign(priv, ex.canonical_payload_bytes(payload)), payload["nonce"])

    monkeypatch.setattr(ex, "request_device_approval", device)
    assert client.post("/requests/pr_sub_15_6/pay", json=CONFIRM).status_code == 200
    _, req = decided(world, "pr_sub_15_6")
    assert sent["bank_account"] == req.bank_account and sent["expires_at"] - __import__("time").time() > 100
