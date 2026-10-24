"""The dashboard's API over a real (temporary) event log."""

import pytest
from fastapi.testclient import TestClient

from api import main
from events.replay import load_seed
from seed.anomalies import with_demo
from seed.generate import generate


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "events.db"
    load_seed(with_demo(generate())[0], path=db)
    monkeypatch.setattr(main, "DB", db)
    monkeypatch.setattr(main, "SCORECARD", tmp_path / "scorecard.json")
    monkeypatch.setattr(main, "CALIBRATION", tmp_path / "calibration.json")
    # the real kernel (all 15 rules): it holds the three demo stories on its own
    return TestClient(main.app)


def test_list_before_and_after_replay(client):
    rows = client.get("/requests", params={"month": 6}).json()
    assert len(rows) == 15 and {r["status"] for r in rows} == {"not_checked"}
    assert client.post("/replay", params={"through": 6}).json() == {"new_decisions": 90}
    rows = {r["id"]: r for r in client.get("/requests").json()}
    statuses = [r["status"] for r in rows.values()]
    assert statuses.count("on_hold") == 3 and statuses.count("ready") == 10 and statuses.count("needs_ok") == 2
    assert rows["pr_sub_01_6"]["sentence"] == "On hold: 1 supplier(s) filed a notice to owner and haven't released."
    assert rows["pr_sub_10_6"]["sentence"] == "Needs your OK because it's over the auto-release limit."
    assert client.post("/replay").json() == {"new_decisions": 0}


def test_request_detail_shows_video_next_to_claim(client):
    client.post("/replay")
    d = client.get("/requests/pr_sub_03_6").json()
    line = next(l for l in d["lines"] if l["item_no"] == "09-20")
    assert (line["pct_complete"], line["video_pct"]) == ("70", "45")
    assert any(c["rule"] == "13_progress_plausible" and not c["passed"] for c in d["checklist"])
    assert d["trace"]["action"] == "hold"
    assert client.get("/requests/nope").status_code == 404


def test_event_stream(client):
    body = client.get("/events", params={"after": 0, "follow": False}).text
    assert body.count("event: fact") == len(body.split("\n\n")) - 1 > 400
    last = int(body.strip().split("\n\n")[-1].split("\n")[0].removeprefix("id: "))
    assert client.get("/events", params={"after": last, "follow": False}).text == ""


def test_attack_console(client):
    names = {a["name"] for a in client.get("/attacks").json()}
    assert "bank_change_email" in names
    out = client.post("/attacks", json={"name": "bank_change_email", "target": "sub_02",
                                        "settings": {"homoglyph": True}}).json()
    assert out["label"]["rules"] == ["rule_10", "rule_11"] and "caught" in out
    assert client.post("/attacks", json={"name": "bank_change_email", "target": "sub_99"}).status_code == 422
    assert client.post("/attacks", json={"name": "nope", "target": "sub_02"}).status_code == 404


def test_scorecard_missing(client):
    assert client.get("/scorecard").status_code == 404
    assert client.get("/calibration").status_code == 404


def test_escalation_reason_and_vendor(client):
    client.post("/replay")
    rows = {r["id"]: r for r in client.get("/requests").json()}
    assert rows["pr_sub_10_6"]["reason"] == "it's over the auto-release limit"
    assert rows["pr_sub_10_6"]["sentence"] == "Needs your OK because it's over the auto-release limit."
    assert client.get("/requests/pr_sub_01_6").json()["vendor"]["email_domain"] == "coastalconcrete.com"


def test_approve_flow(client, monkeypatch, tmp_path):
    """Escalated request -> device signs -> executor pays; a refusal comes back in words."""
    import time as _time
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from kernel import executor

    client.post("/replay")
    assert client.post("/requests/pr_sub_02_6/approve").status_code == 409  # ready, not escalated
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint).hex()
    monkeypatch.setenv("DEVICE_URL", "http://drawkey.test:8001")
    monkeypatch.setattr("kernel.contract.load_contract", lambda: {"auto_release_limit_usd": 25000, "esp32_pubkey": pub})

    def device(url, payload, amount=None):
        p = payload if amount is None else {**payload, "amount": amount}
        r, s = decode_dss_signature(priv.sign(executor.canonical_payload_bytes(p), ec.ECDSA(hashes.SHA256())))
        return executor.SignedApproval(payload=p, sig_hex=r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex(), nonce=p["nonce"])

    paid = []
    monkeypatch.setattr(executor, "_nessie_pay", lambda req, amt: paid.append(amt) or {"request_id": req.id, "amount": str(amt)})
    monkeypatch.setattr(executor, "request_device_approval", device)
    # real kernel decides pr_sub_10_6 escalates (over limit); fake kernel in this fixture does too
    out = client.post("/requests/pr_sub_10_6/approve").json()
    assert out["paid"] is True and len(paid) == 1
    monkeypatch.setattr(executor, "request_device_approval", lambda url, p: device(url, p, amount="1.00"))
    out = client.post("/requests/pr_sub_15_6/approve").json()
    assert out["paid"] is False and "signed amount" in out["reason"] and len(paid) == 1


def test_event_stream_from_now(client):
    assert client.get("/events", params={"after": -1, "follow": False}).text == ""
