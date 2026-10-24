"""New subcontractors from the Upload screen: POST /vendors, then a first pay app whose SOV comes from the PDF."""
import random
from datetime import date
from decimal import Decimal

import pipeline
from api import main
from events import log as events
from seed import anomalies as a
from shared.schema import COI, Action, Release, ReleaseKind
from synthetic import render
from tests.test_api_ingest import client, upload, world  # noqa: F401  (fixtures)

BANK = "123456789:1234567890"


def new_vendor(client, name="Apex Concrete Services LLC", **extra):
    return client.post("/vendors", json={"legal_name": name, **extra})


def first_pay_app(world, tmp_path, sub_id, legal_name, job_id="job_1"):
    """A small first G703 from a brand-new sub, well under the auto-release limit."""
    data, _ = a.new_sub_ein_mismatch(world, job_id, random.Random(1), name=legal_name)
    req = data["pay_requests"][-1]
    small = [l.model_copy(update={"scheduled_value": v, "balance_to_finish": v})
             for l, v in zip(req.lines, (Decimal("10000.00"), Decimal("8000.00"), Decimal("6000.00")))]
    req = a.rebill(req.model_copy(update={"sub_id": sub_id, "lines": small, "bank_account": BANK}),
                   {l.item_no: Decimal(30) for l in small})
    vendor = data["vendors"][-1].model_copy(update={"id": sub_id})
    path = tmp_path / "first.pdf"
    render.g703_pdf(req, vendor, next(j for j in world["jobs"] if j.id == job_id), path, "accounting")
    return req, path


def test_create_vendor_appends_a_fact(client):
    r = new_vendor(client, ein="12-3456789", email_domain="@ApexConcrete.com")
    assert r.status_code == 200, r.text
    vendor = next(v for v in events.state(path=main.DB)["vendors"] if v.id == r.json()["id"])
    assert (vendor.legal_name, vendor.crs_status, vendor.first_paid) == ("Apex Concrete Services LLC", "unknown", None)
    assert (vendor.ein, vendor.email_domain, vendor.bank_account) == ("12-3456789", "apexconcrete.com", "")
    listed = client.get("/vendors").json()
    assert {"id": vendor.id, "legal_name": vendor.legal_name, "job_ids": []} in listed["vendors"]
    assert listed["jobs"] and all({"id", "name"} <= j.keys() for j in listed["jobs"])


def test_create_vendor_rejects_duplicates_and_blank_names(client, world):
    existing = world["vendors"][0].legal_name
    r = new_vendor(client, name=f"  {existing.upper()} ")
    assert r.status_code == 409 and "already a subcontractor" in r.json()["detail"]
    r = new_vendor(client, name="   ")
    assert r.status_code == 422 and r.json()["detail"] == "Enter the subcontractor's legal name."
    assert len(events.state(path=main.DB)["vendors"]) == len(world["vendors"])


def test_create_vendor_needs_the_upload_passcode(client, monkeypatch):
    monkeypatch.setenv("UPLOAD_PASSCODE", "s3cret")
    assert new_vendor(client).status_code == 401
    assert client.post("/vendors", json={"legal_name": "X Co"}, headers={"X-Upload-Passcode": "nope"}).status_code == 401
    assert client.post("/vendors", json={"legal_name": "X Co"}, headers={"X-Upload-Passcode": "s3cret"}).status_code == 200
    monkeypatch.delenv("UPLOAD_PASSCODE")
    monkeypatch.setenv("VERCEL", "1")  # deployed with no passcode set: off
    assert new_vendor(client, name="Y Co").status_code == 401


def test_new_subs_first_pay_app_holds_then_escalates_never_releases(client, world, tmp_path):
    sub_id = new_vendor(client, bank_account=BANK).json()["id"]
    req, path = first_pay_app(world, tmp_path, sub_id, "Apex Concrete Services LLC")
    assert req.g702.current_payment_due < 25000  # under the auto-release limit, so only "new sub" can escalate it

    r = upload(client, path, sub_id, "job_1")
    assert r.status_code == 200, r.text
    rid = r.json()["request_id"]
    state = events.state(path=main.DB)
    sc = next(c for c in state["subcontracts"] if c.sub_id == sub_id)
    assert sc.id == f"sc_from_pay_app_{sub_id}_job_1" and [l.scheduled_value for l in sc.sov] == [l.scheduled_value for l in req.lines]

    held = pipeline.decide(state, rid)
    assert held.action == Action.hold
    assert {x.rule[:2] for x in held.results if not x.passed} == {"09", "06", "04"}

    stored = next(p for p in state["pay_requests"] if p.id == rid)
    vendor = next(v for v in state["vendors"] if v.id == sub_id)
    events.append("fact", vendor.model_copy(update={"crs_status": "verified"}), table="vendors", path=main.DB)
    events.append("fact", COI(id=f"coi_{sub_id}", sub_id=sub_id, gl_expiry=date(2027, 6, 30), wc_expiry=date(2027, 6, 30),
                              additional_insured=True), table="cois", path=main.DB)
    events.append("fact", Release(id=f"rel_c_{rid}", sub_id=sub_id, job_id="job_1", kind=ReleaseKind.conditional_progress,
                                  through_date=stored.period_end, amount=stored.g702.current_payment_due, signed=True,
                                  received_at=date(2026, 10, 24)), table="releases", path=main.DB)
    state = events.state(path=main.DB)
    ok = pipeline.decide(state, rid)
    assert ok.action == Action.escalate, [x for x in ok.results if not x.passed]
    assert all(x.passed for x in ok.results)

    events.append("kernel_trace", ok, request_id=rid, path=main.DB)
    row = main.row(stored, state, ok)
    assert row["status"] == "needs_ok" and row["new_sub"] and row["new_sub_note"] == main.NEW_SUB_NOTE
    assert "first payment to this sub" in row["reason"]


def test_new_sub_without_a_bank_account_on_file_is_also_held_on_rule_10(client, world, tmp_path):
    sub_id = new_vendor(client).json()["id"]
    _, path = first_pay_app(world, tmp_path, sub_id, "Apex Concrete Services LLC")
    rid = upload(client, path, sub_id, "job_1").json()["request_id"]
    held = pipeline.decide(events.state(path=main.DB), rid)
    assert {x.rule[:2] for x in held.results if not x.passed} == {"09", "06", "04", "10"}


def test_unreadable_pdf_from_a_new_sub_appends_nothing(client, tmp_path):
    sub_id = new_vendor(client).json()["id"]
    before = len(events.read(path=main.DB))
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pay application")
    r = upload(client, bad, sub_id, "job_1")
    assert r.status_code == 422, r.text
    assert len(events.read(path=main.DB)) == before
    assert not any(c.sub_id == sub_id for c in events.state(path=main.DB)["subcontracts"])
