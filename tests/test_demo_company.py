"""The demo company (seed/demo_company.py): on file before its first pay application, which then
arrives by email, is read, passes every check, and needs the owner's OK for being large and first."""
import base64
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api import main
from events.replay import load_seed, replay
from seed import demo_company
from seed.anomalies import with_demo
from seed.generate import generate
from tests.perception.test_spreadsheet_export import export_g702, export_g703

ROWS = [  # 10% retainage on every line, like the demo world's contracts
    ("1", "00 - Contract Requirements", {"D": "$0.00", "E": "$0.00", "F": "$0.00", "I": "$0.00"}),
    ("2", "007316.13 - Builder's Risk", {"C": "$81,851.00", "D": "$0.00", "E": "$81,851.00", "F": "$0.00",
                                         "G": "$81,851.00", "%": "100.00%", "I": "$8,185.10"}),
    ("3", "142000 - Elevators", {"C": "$280,000.00", "D": "$0.00", "E": "$140,000.00", "F": "$0.00",
                                 "G": "$140,000.00", "%": "50.00%", "H": "$140,000.00", "I": "$14,000.00"}),
    ("4", "Total Project", {"C": "$361,851.00", "D": "$0.00", "E": "$221,851.00", "F": "$0.00", "G": "$221,851.00",
                            "%": "61.31%", "H": "$140,000.00", "I": "$22,185.10"}),
]
AMOUNTS = {"contract": "361,851.00", "completed": "221,851.00", "retainage": "22,185.10", "earned": "199,665.90",
           "balance": "162,185.10"}
DOMAIN = "example-builders.com"


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "demo_company"
    f.mkdir()
    export_g703(f / "g703.pdf", rows=ROWS, period="10.25.2026")
    export_g702(f / "g702.pdf", amounts=AMOUNTS, period="10.25.2026")
    (f / "company.json").write_text(json.dumps({
        "sub_id": "sub_16", "legal_name": "Example Builders LLC", "email_domain": DOMAIN, "ein": "12-3456789",
        "job": {"id": "job_4", "name": "Example Hotel", "owner": "Example Owner LP", "lender": "First Pacific Bank",
                "architect": "Example Design"},
        "g703": "g703.pdf", "g702": "g702.pdf", "site_video": {"date": "2026-10-22", "estimates": {"3": 45}}}))
    return f


def test_the_company_is_on_file_without_its_pay_application(folder):
    data = demo_company.add(with_demo(generate())[0], folder)
    assert [j.name for j in data["jobs"]][-1] == "Example Hotel"
    contract = next(c for c in data["subcontracts"] if c.sub_id == "sub_16")
    assert [(s.item_no, str(s.scheduled_value)) for s in contract.sov] == [("2", "81851.00"), ("3", "280000.00")]
    assert next(v for v in data["vendors"] if v.id == "sub_16").bank_account == "021000021:77812345"
    assert not [r for r in data["pay_requests"] if r.sub_id == "sub_16"]
    release = next(r for r in data["releases"] if r.sub_id == "sub_16")
    assert str(release.amount) == "199665.90" and str(release.through_date) == "2026-10-25"


def test_the_committed_demo_company_reads_its_own_pay_application():
    """The PDFs to email (seed/demo_company_files/) and the contract on file agree, and fit the demo month."""
    data = demo_company.facts()
    vendor, contract, release = data["vendors"][0], data["subcontracts"][0], data["releases"][0]
    assert vendor.legal_name == "Larkspur Construction LLC" and vendor.email_domain == "stanford.edu"
    assert len(contract.sov) == 115 and sum(s.scheduled_value for s in contract.sov) == Decimal("18750487.90")
    assert release.amount == Decimal("595008.60") and str(release.through_date) == "2026-10-25"
    assert data["ledger"][0].period_end == release.through_date  # the owner's draw for that period


def test_without_its_folder_the_seed_is_unchanged(tmp_path):
    assert demo_company.facts(tmp_path / "nothing here") is None
    assert demo_company.add(with_demo(generate())[0], tmp_path) == with_demo(generate())[0]


def test_its_emailed_pay_application_needs_the_owners_ok(folder, tmp_path, monkeypatch):
    db = tmp_path / "events.db"
    load_seed(demo_company.add(with_demo(generate())[0], folder), path=db)
    replay(6, path=db)  # what `make reset` leaves: May-October decided, the sub's pay app not yet in
    monkeypatch.setattr(main, "DB", db)
    monkeypatch.setattr(main, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setenv("INBOUND_PASSWORD", "s3cret")
    client = TestClient(main.app)
    payload = {"MessageID": "demo", "From": f"ap@{DOMAIN}", "To": "draw@inbound.postmarkapp.com",
               "Subject": "Pay application #1", "Date": "Sat, 24 Oct 2026 17:00:00 -0700", "TextBody": "Attached.",
               "Attachments": [{"Name": p.name, "ContentType": "application/pdf",
                                "Content": base64.b64encode(p.read_bytes()).decode()}
                               for p in (folder / "g702.pdf", folder / "g703.pdf")]}
    auth = base64.b64encode(b"postmark:s3cret").decode()
    r = client.post("/inbound/postmark", json=payload, headers={"Authorization": f"Basic {auth}"})
    assert [(a["kind"], a["status"]) for a in r.json()["attachments"]] == [("g702", "used"), ("pay_app", "recorded")]
    request_id = r.json()["attachments"][1]["result"]["request_id"]

    assert client.post("/replay?through=6").json()["new_decisions"] == 1  # the dashboard's "Check October"
    d = client.get(f"/requests/{request_id}").json()
    assert d["status"] == "needs_ok" and d["amount"] == "199665.90"
    assert d["failed_rules"] == ["14"] and d["why"] == ["over_limit", "new_vendor"]
