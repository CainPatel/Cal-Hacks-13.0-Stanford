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


def test_rows_carry_what_the_colour_categories_need(client):
    assert client.get("/requests").json()[0]["failed_rules"] == []  # not checked yet
    client.post("/replay")
    rows = {r["id"]: r for r in client.get("/requests").json()}
    assert {"10", "11"} <= set(rows["pr_sub_04_6"]["failed_rules"])  # Brightline: bank change from a lookalike domain
    assert rows["pr_sub_01_6"]["failed_rules"] == ["07", "14"]  # Coastal: supplier hasn't released, and over the limit
    assert rows["pr_sub_10_6"]["why"] == ["over_limit"] and rows["pr_sub_01_6"]["why"] == []
    assert rows["pr_sub_10_6"]["limit"] == str(main.load_contract()["auto_release_limit_usd"])
    assert {r["crs_status"] for r in rows.values()} <= {"verified", "mismatch", "flagged", "unknown"}


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


def test_scorecard_scored_on_first_request(client, monkeypatch):
    monkeypatch.setenv("SCORECARD_CASES", "40")
    card = client.get("/scorecard").json()
    assert card["cases"] == 40 and "limit_sweep" in card
    assert main.SCORECARD.exists()  # cached for the next request


def test_uploads_archived_privately_when_blob_is_connected(client, monkeypatch):
    from vercel.blob import sync as blob
    stored = []
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-token")
    monkeypatch.setattr(blob, "put_blob", lambda path, data, **kw: stored.append((path, kw)) or type("R", (), {"pathname": path})())
    assert main._archive(b"%PDF-1.4 test", "abc.pdf") == "uploads/abc.pdf"
    assert stored == [("uploads/abc.pdf", {"access": "private", "allow_overwrite": True})]
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN")
    assert main._archive(b"x", "abc.pdf") is None  # no store connected: local only
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
    monkeypatch.setattr("shared.contract.load_contract", lambda: {"auto_release_limit_usd": 25000, "esp32_pubkey": pub})

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


def test_status_page_and_unconfigured_log(client, monkeypatch):
    body = client.get("/").json()
    assert body["event_log"] == "sqlite" and body["log_has_events"] is True
    assert all(isinstance(v, bool) for k, v in body["configured"].items() if k not in ("cors_origins", "jev_route"))  # never secret values
    from events import log as events
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(main, "DB", events.DEFAULT_PATH)
    r = client.get("/requests")
    assert r.status_code == 503 and "Neon" in r.json()["detail"]
    assert client.get("/").json()["event_log"] == "not connected"


def test_setup_demo_only_into_an_empty_log(client, tmp_path, monkeypatch):
    assert client.post("/setup/demo").status_code == 409  # fixture already loaded the seed
    monkeypatch.setattr(main, "DB", tmp_path / "fresh.db")
    assert client.post("/setup/demo").json() == {"facts_loaded": 419, "decisions": 90}
    assert len(client.get("/requests").json()) == 15
    assert client.post("/setup/demo").status_code == 409  # once only


def test_status_reports_the_active_jev_route(client, monkeypatch):
    for k in ("JEV_BASE_URL", "TYPESAFE_API_KEY", "JEV_API_KEY", "AI_GATEWAY_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JEV_BASE_URL", "https://api.typesafe.ai")
    body = client.get("/").json()["configured"]
    assert body["jev_route"] == "api.typesafe.ai" and body["jev_key"] is False
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "x")  # the gateway key doesn't count for TypeSafe's route
    assert client.get("/").json()["configured"]["jev_key"] is False
    monkeypatch.setenv("TYPESAFE_API_KEY", "x")
    assert client.get("/").json()["configured"]["jev_key"] is True
