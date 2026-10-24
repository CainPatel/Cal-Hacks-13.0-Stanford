"""The dashboard's API over a real (temporary) event log."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import pipeline
from api import main
from events.replay import load_seed
from seed.anomalies import with_demo
from seed.generate import generate
from shared.schema import Action, Decision, RuleResult

DEMO_HOLDS = {"pr_sub_01_6", "pr_sub_03_6", "pr_sub_04_6"}


def fake_kernel(state, rid):
    """Holds the demo stories, escalates over $25K, releases the rest, like the real kernel would."""
    req = next(r for r in state["pay_requests"] if r.id == rid)
    if rid in DEMO_HOLDS:
        return Decision(request_id=rid, action=Action.hold, next_action="Ask the sub.", results=[
            RuleResult(rule="07_lower_tier_released", passed=False, evidence={},
                       message="Westside Ready-Mix filed a notice to owner and hasn't released.")])
    action = Action.escalate if req.g702.current_payment_due > Decimal(25000) else Action.release
    return Decision(request_id=rid, action=action, results=[])


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "events.db"
    load_seed(with_demo(generate())[0], path=db)
    monkeypatch.setattr(main, "DB", db)
    monkeypatch.setattr(main, "SCORECARD", tmp_path / "scorecard.json")
    monkeypatch.setattr(main, "CALIBRATION", tmp_path / "calibration.json")
    monkeypatch.setattr(pipeline, "decide", fake_kernel)
    return TestClient(main.app)


def test_list_before_and_after_replay(client):
    rows = client.get("/requests", params={"month": 6}).json()
    assert len(rows) == 15 and {r["status"] for r in rows} == {"not_checked"}
    assert client.post("/replay", params={"through": 6}).json() == {"new_decisions": 90}
    rows = {r["id"]: r for r in client.get("/requests").json()}
    statuses = [r["status"] for r in rows.values()]
    assert statuses.count("on_hold") == 3 and statuses.count("ready") == 10 and statuses.count("needs_ok") == 2
    assert rows["pr_sub_01_6"]["sentence"] == "On hold: Westside Ready-Mix filed a notice to owner and hasn't released."
    assert rows["pr_sub_10_6"]["sentence"].startswith("Needs your OK: $") and "Cascade Mechanical" in rows["pr_sub_10_6"]["sentence"]
    assert client.post("/replay").json() == {"new_decisions": 0}


def test_request_detail_shows_video_next_to_claim(client):
    client.post("/replay")
    d = client.get("/requests/pr_sub_03_6").json()
    line = next(l for l in d["lines"] if l["item_no"] == "09-20")
    assert (line["pct_complete"], line["video_pct"]) == ("70", "45")
    assert d["checklist"][0]["passed"] is False and d["trace"]["action"] == "hold"
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
