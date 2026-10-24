from types import SimpleNamespace

import pytest

import crs
from seed.generate import generate


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(crs, "_token", None)
    monkeypatch.setattr(crs, "_login_refused", None)
    for k in ("CRS_API_KEY", "CRS_USERNAME", "CRS_PASSWORD", "CRS_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def biid(name="true", fein="true", ofac=(), watch=(), wrap=True):
    company = {"verificationIndicators": {"companyName": name, "fein": fein},
               "businessVerification": {"index": "50", "description": "Verified"},
               "compliance": {"sosstatus": "Active", "globalWatchLists": {"globalWatchList": list(watch)}},
               "ofacwatchLists": {"ofacwatchList": list(ofac)},
               "riskIndicators": {"riskIndicator": [{"riskCode": "30", "description": "Address is a mail drop"}]}}
    body = {"result": {"companyResults": company}}
    return {"response": body} if wrap else body


@pytest.mark.parametrize("body,status", [
    (biid(), "verified"),
    (biid(wrap=False), "verified"),
    (biid(fein="false"), "mismatch"),
    (biid(name="0"), "mismatch"),
    (biid(ofac=[{"entityName": "ACME FRONT CO", "table": "SDN"}]), "flagged"),
    (biid(watch=[{"entityName": "X"}], fein="false"), "flagged"),  # a watchlist hit outranks a mismatch
    (biid(name="", fein=""), "unknown"),
    ({"response": {"result": {}}}, "unknown"),
    ("not a dict", "unknown"),
])
def test_interpret(body, status):
    assert crs.interpret(body).status == status


def test_evidence_is_kept():
    r = crs.interpret(biid(ofac=[{"entityName": "ACME FRONT CO"}]))
    assert r.evidence["watchlist_hits"] == ["ACME FRONT CO"] and r.evidence["secretary_of_state_status"] == "Active"
    assert r.evidence["risk_indicators"] == ["30: Address is a mail drop"]


class FakeHTTP:
    def __init__(self, login=200, check=200, body=None):
        self.calls, self.login, self.check, self.body = [], login, check, body or biid()

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(SimpleNamespace(url=url, json=json, headers=headers))
        if url.endswith("/users/login"):
            return SimpleNamespace(status_code=self.login, text="", json=lambda: {"token": "tok", "expires": 0})
        return SimpleNamespace(status_code=self.check, text="nope", json=lambda: self.body)


def test_logs_in_once_and_sends_the_business_check(monkeypatch):
    monkeypatch.setenv("CRS_USERNAME", "team")
    monkeypatch.setenv("CRS_PASSWORD", "pw")
    http = FakeHTTP()
    assert crs.verify_business("Coastal Concrete Inc.", "12-3456789", http=http).status == "verified"
    crs.verify_business("Coastal Concrete Inc.", "12-3456789", http=http)
    logins = [c for c in http.calls if c.url.endswith("/users/login")]
    checks = [c for c in http.calls if c.url.endswith("/vfps/biid2")]
    assert len(logins) == 1 and len(checks) == 2
    assert checks[0].url == "https://api-sandbox.stitchcredit.com/api/vfps/biid2"
    assert checks[0].headers == {"Authorization": "Bearer tok"}
    assert checks[0].json["searchBy"]["company"] == {"companyName": "Coastal Concrete Inc.", "fein": "123456789"}
    assert checks[0].json["options"]["includeOFAC"] is True


def test_refused_login_is_never_retried(monkeypatch):
    monkeypatch.setenv("CRS_USERNAME", "team")
    monkeypatch.setenv("CRS_PASSWORD", "wrong")
    http = FakeHTTP(login=401)
    for _ in range(3):
        with pytest.raises(crs.CRSError):
            crs.verify_business("A Co", "123456789", http=http)
    assert len([c for c in http.calls if c.url.endswith("/users/login")]) == 1


def test_api_key_is_used_as_the_bearer_token(monkeypatch):
    monkeypatch.setenv("CRS_API_KEY", "direct")
    http = FakeHTTP()
    crs.verify_business("A Co", "123456789", http=http)
    assert http.calls[0].headers == {"Authorization": "Bearer direct"} and len(http.calls) == 1


def test_product_not_enabled_is_explained(monkeypatch):
    monkeypatch.setenv("CRS_API_KEY", "direct")
    with pytest.raises(crs.CRSError, match="not have that product enabled"):
        crs.verify_business("A Co", "123456789", http=FakeHTTP(check=403))


def test_bad_ein_is_not_sent(monkeypatch):
    monkeypatch.setenv("CRS_API_KEY", "direct")
    http = FakeHTTP()
    assert crs.verify_business("A Co", "12-34", http=http).status == "unknown" and not http.calls


def test_no_credentials(monkeypatch):
    with pytest.raises(crs.CRSError, match="no CRS credentials"):
        crs.verify_business("A Co", "123456789", http=FakeHTTP())


def test_check_vendor_sets_status_and_keeps_it_through_an_outage(monkeypatch):
    monkeypatch.setenv("CRS_API_KEY", "direct")
    vendor = generate()["vendors"][0]
    updated, result, error = crs.check_vendor(vendor.model_copy(update={"crs_status": "unknown"}),
                                              http=FakeHTTP(body=biid(fein="false")))
    assert updated.crs_status == "mismatch" and result.status == "mismatch" and error is None
    same, result, error = crs.check_vendor(vendor, http=FakeHTTP(check=500))
    assert same == vendor and result is None and "HTTP 500" in error


def test_verify_endpoint_records_a_changed_status(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api import main
    from events import log as events
    from events.replay import load_seed
    from seed.anomalies import with_demo

    db = tmp_path / "events.db"
    load_seed(with_demo(generate())[0], path=db)
    monkeypatch.setattr(main, "DB", db)
    monkeypatch.setenv("CRS_API_KEY", "direct")
    monkeypatch.setattr(crs.requests, "post", FakeHTTP(body=biid(ofac=[{"entityName": "FRONT CO"}])).post)
    r = TestClient(main.app).post("/vendors/sub_01/verify")
    assert r.status_code == 200 and r.json()["crs_status"] == "flagged"
    assert next(v for v in events.state(path=db)["vendors"] if v.id == "sub_01").crs_status == "flagged"
    monkeypatch.setattr(crs.requests, "post", FakeHTTP(check=500).post)
    assert TestClient(main.app).post("/vendors/sub_01/verify").status_code == 502
    assert next(v for v in events.state(path=db)["vendors"] if v.id == "sub_01").crs_status == "flagged"
