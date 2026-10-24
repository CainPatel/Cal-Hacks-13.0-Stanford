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
    vendor = generate()["vendors"][0].model_copy(update={"id": "sub_unmapped"})  # no owner on file: business check
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
    monkeypatch.setattr(crs, "principal_for", lambda vendor_id: None)  # force the business check for this test
    monkeypatch.setattr(crs.requests, "post", FakeHTTP(body=biid(ofac=[{"entityName": "FRONT CO"}])).post)
    r = TestClient(main.app).post("/vendors/sub_01/verify")
    assert r.status_code == 200 and r.json()["crs_status"] == "flagged"
    assert next(v for v in events.state(path=db)["vendors"] if v.id == "sub_01").crs_status == "flagged"
    monkeypatch.setattr(crs.requests, "post", FakeHTTP(check=500).post)
    assert TestClient(main.app).post("/vendors/sub_01/verify").status_code == 502
    assert next(v for v in events.state(path=db)["vendors"] if v.id == "sub_01").crs_status == "flagged"


# --- the person behind the vendor (TU identity verification + Fraud Finder) ---------------------------


def idv(*codes, hit="HIT"):
    msgs = [{"code": c, "text": "msg", "source": "INPUT"} for c in codes]
    return {"fileNumber": "01", "addOnProducts": [{"idManagerVerification": {"searchStatus": hit, "messages": msgs}}]}


def fraud(score):
    return {"eam": {"date_first_seen": "2012-09-16"}, "risk": {"score": score}}


@pytest.mark.parametrize("identity,risk,status", [
    (idv(), fraud(9), "verified"),                       # P19: clean
    (idv("2201", "2202", "2101"), fraud(9), "mismatch"),  # P12: SSN belongs to someone else, DOB off
    (idv("2301", "2101"), fraud(9), "flagged"),           # P14: deceased
    (idv(), fraud(88), "flagged"),                        # P13: synthetic identity
    (idv("2101"), fraud(9), "unknown"),                   # address only: a person decides
    (idv(), fraud(55), "unknown"),                        # middling fraud risk
    (idv(hit="NO_HIT"), fraud(9), "unknown"),
])
def test_interpret_principal(identity, risk, status):
    assert crs.interpret_principal(identity, risk).status == status


def test_every_vendor_has_an_owner_on_file_and_the_fraud_cases_map_to_bad_personas():
    from seed.anomalies import cases, with_demo
    data = with_demo(generate())[0]
    ids = {v.id for v in data["vendors"]} | {v.id for d, _ in cases() for v in d["vendors"]}
    assert all(crs.principal_for(i) for i in ids)
    assert crs.principal_for("sub_new_job_1")["persona"] == "P12" and crs.principal_for("sub_new_job_3")["persona"] == "P13"


class ProductHTTP:
    """Answers the two consumer products like the sandbox does for a given persona."""

    def __init__(self, identity, risk):
        self.calls, self.identity, self.risk = [], identity, risk

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(SimpleNamespace(url=url, json=json))
        if url.endswith("/users/login"):
            return SimpleNamespace(status_code=200, text="", json=lambda: {"token": "tok"})
        body = self.identity if "identity-manager" in url else self.risk
        return SimpleNamespace(status_code=200, text="", json=lambda: body)


def test_check_vendor_uses_the_owner_when_one_is_on_file(monkeypatch):
    monkeypatch.setenv("CRS_API_KEY", "direct")
    vendor = generate()["vendors"][0]
    http = ProductHTTP(idv("2201"), fraud(9))
    updated, result, error = crs.check_vendor(vendor, http=http)
    assert (updated.crs_status, error) == ("mismatch", None)
    idv_call, ff_call = http.calls
    owner = crs.principal_for(vendor.id)
    assert idv_call.url.endswith("/transunion/identity-manager-verification/basic")
    assert idv_call.json["ssn"] == str(owner["ssn"]) and idv_call.json["lastName"] == owner["last_name"]
    assert ff_call.url.endswith("/fraud-finder/fraud-finder") and ff_call.json["email"] == owner["email"]
