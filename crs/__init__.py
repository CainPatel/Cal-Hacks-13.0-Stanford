"""Vendor identity checks against the CRS Credit API sandbox (rule 09's crs_status). Owner: C.

Two ways to check a vendor, tried in this order:

1. The person behind it (verify_principal), for vendors with an owner on file in principals.yaml:
   TransUnion identity verification on the owner's name, SSN, date of birth and address, plus
   Fraud Finder on the owner's email and phone. Small subs are mostly owner-operated, so this is
   the identity that matters, and both products are enabled on the team's sandbox account.
2. The business itself (verify_business): LexisNexis Business InstantID (POST /vfps/biid2) with
   the legal name and EIN, OFAC screening on. CRS has not enabled it for the team's account (401),
   so it is used only for vendors without an owner on file.

Either way the answer becomes one of the statuses rule 09 knows:

    flagged   any OFAC or global watchlist hit
    mismatch  CRS says the legal name or the EIN doesn't verify
    verified  both verify and there are no watchlist hits
    unknown   anything else (rule 09 holds, so an unclear answer never pays)

Credentials: CRS_USERNAME / CRS_PASSWORD (logged in once per process; a refused login is never
retried, because repeated failures lock the CRS account), or CRS_API_KEY holding a bearer token.
Base URL: CRS_BASE_URL, default the sandbox. Which products an account may call is set by CRS;
a 401/403 on the check itself usually means Business InstantID isn't enabled for the team.
"""

import os
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import requests
import yaml

from shared.schema import Vendor

SANDBOX = "https://api-sandbox.stitchcredit.com/api"
TIMEOUT_S = 30
_YES = {"true", "t", "1", "y", "yes", "verified", "match", "matched"}
_NO = {"false", "f", "0", "n", "no", "not verified", "nomatch", "no match", "mismatch"}


class CRSError(RuntimeError):
    pass


@dataclass
class CRSResult:
    status: str  # verified | mismatch | flagged | unknown
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


_token: str | None = None
_login_refused: str | None = None


def _base() -> str:
    return os.environ.get("CRS_BASE_URL", SANDBOX).rstrip("/")


def _login(http=requests) -> str:
    """A bearer token: CRS_API_KEY as-is, else one login with CRS_USERNAME/CRS_PASSWORD, cached."""
    global _token, _login_refused
    if os.environ.get("CRS_API_KEY"):
        return os.environ["CRS_API_KEY"]
    if _token:
        return _token
    if _login_refused:
        raise CRSError(f"not logging in again after a refused login ({_login_refused}); fix the credentials and restart")
    user, password = os.environ.get("CRS_USERNAME"), os.environ.get("CRS_PASSWORD")
    if not (user and password):
        raise CRSError("no CRS credentials configured (CRS_USERNAME and CRS_PASSWORD, or CRS_API_KEY)")
    try:
        resp = http.post(f"{_base()}/users/login", json={"username": user, "password": password}, timeout=TIMEOUT_S)
    except requests.exceptions.RequestException as e:
        raise CRSError(f"could not reach CRS: {e}") from e
    if resp.status_code >= 400:
        _login_refused = f"HTTP {resp.status_code}"
        raise CRSError(f"CRS refused the login (HTTP {resp.status_code}); not retrying, repeated failures lock the account")
    token = (resp.json() or {}).get("token")
    if not token:
        raise CRSError("CRS login answered without a token")
    _token = token
    return token


def _indicator(value) -> bool | None:
    text = str(value).strip().lower() if value is not None else ""
    return True if text in _YES else False if text in _NO else None


def _items(container, key: str) -> list:
    if not isinstance(container, dict):
        return []
    inner = container.get(key)
    return inner if isinstance(inner, list) else [inner] if isinstance(inner, dict) else []


def interpret(body: dict) -> CRSResult:
    """Map a Business InstantID response onto rule 09's statuses. Pure; tested against shaped samples."""
    root = body.get("response", body) if isinstance(body, dict) else {}
    company = ((root.get("result") or {}).get("companyResults")) if isinstance(root, dict) else None
    if not isinstance(company, dict):
        return CRSResult("unknown", ["CRS returned no company results"], {})
    vi = company.get("verificationIndicators") or {}
    name_ok, fein_ok = _indicator(vi.get("companyName")), _indicator(vi.get("fein"))
    compliance = company.get("compliance") or {}
    ofac = _items(company.get("ofacwatchLists"), "ofacwatchList")
    global_hits = _items(compliance.get("globalWatchLists"), "globalWatchList")
    risk = _items(company.get("riskIndicators"), "riskIndicator")
    bvi = (company.get("businessVerification") or {})
    evidence = {
        "name_verified": name_ok, "fein_verified": fein_ok,
        "business_verification_index": bvi.get("index"), "business_verification": bvi.get("description"),
        "secretary_of_state_status": compliance.get("sosstatus"),
        "watchlist_hits": [h.get("entityName") or h.get("table") or str(h)[:80] for h in ofac + global_hits],
        "risk_indicators": [f"{r.get('riskCode')}: {r.get('description')}" for r in risk if isinstance(r, dict)],
    }
    if ofac or global_hits:
        return CRSResult("flagged", [f"{len(ofac) + len(global_hits)} watchlist hit(s)"], evidence)
    misses = [what for what, ok in (("legal name", name_ok), ("EIN", fein_ok)) if ok is False]
    if misses:
        return CRSResult("mismatch", [f"{' and '.join(misses)} did not verify"], evidence)
    if name_ok and fein_ok:
        return CRSResult("verified", ["legal name and EIN verified, no watchlist hits"], evidence)
    return CRSResult("unknown", ["CRS didn't say whether the name and EIN verify"], evidence)


def verify_business(legal_name: str, ein: str, http=requests) -> CRSResult:
    fein = re.sub(r"\D", "", ein or "")
    if len(fein) != 9:
        return CRSResult("unknown", [f"EIN {ein!r} is not nine digits; not sent to CRS"], {})
    token = _login(http)
    body = {"searchBy": {"company": {"companyName": legal_name, "fein": fein}}, "options": {"includeOFAC": True}}
    try:
        resp = http.post(f"{_base()}/vfps/biid2", json=body, headers={"Authorization": f"Bearer {token}"},
                         timeout=TIMEOUT_S)
    except requests.exceptions.RequestException as e:
        raise CRSError(f"CRS request did not complete: {e}") from e
    if resp.status_code in (401, 403):
        raise CRSError(f"CRS answered HTTP {resp.status_code} to Business InstantID; the account may not have that "
                       "product enabled (ask CRS) or the token expired")
    if resp.status_code >= 400:
        raise CRSError(f"CRS answered HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return interpret(resp.json())
    except ValueError as e:
        raise CRSError("CRS answered with something other than JSON") from e


# --- the person behind the vendor -----------------------------------------------------------

PRINCIPALS = Path(__file__).parent / "principals.yaml"
FRAUD_FLAGGED = 70  # Fraud Finder risk.score at or above this flags the vendor
FRAUD_CLEAR = 40    # below this is clear; in between, a person decides (unknown)
_IDENTITY_MISMATCH = {"2201", "2202"}  # SSN not associated with the name; date of birth doesn't match


@cache
def _principals() -> dict:
    return yaml.safe_load(PRINCIPALS.read_text()) or {} if PRINCIPALS.exists() else {}


def principal_for(vendor_id: str) -> dict | None:
    return _principals().get(vendor_id)


def _find(node, key: str):
    """First value stored under `key` anywhere in a nested response."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            found = _find(child, key)
            if found is not None:
                return found
    return None


def interpret_principal(identity: dict, fraud: dict) -> CRSResult:
    """Map TU identity verification + Fraud Finder onto rule 09's statuses. Pure."""
    idv = _find(identity, "idManagerVerification") or {}
    messages = [m for m in (idv.get("messages") or []) if isinstance(m, dict)]
    codes = {str(m.get("code")) for m in messages}
    risk = (fraud.get("risk") or {}) if isinstance(fraud, dict) else {}
    score = risk.get("score")
    evidence = {
        "identity_search": idv.get("searchStatus"),
        "identity_messages": [f"{m.get('code')}: {m.get('text')}" for m in messages],
        "fraud_risk_score": score,
        "email_first_seen": (fraud.get("eam") or {}).get("date_first_seen") if isinstance(fraud, dict) else None,
    }
    deceased = any(c.startswith("23") for c in codes)
    if deceased or (isinstance(score, (int, float)) and score >= FRAUD_FLAGGED):
        why = ["the owner's SSN belongs to a deceased person"] if deceased else []
        if isinstance(score, (int, float)) and score >= FRAUD_FLAGGED:
            why.append(f"fraud risk {score} of 100")
        return CRSResult("flagged", why, evidence)
    if codes & _IDENTITY_MISMATCH:
        return CRSResult("mismatch", ["the owner's name, SSN or date of birth don't match"], evidence)
    if idv.get("searchStatus") == "HIT" and not codes and isinstance(score, (int, float)) and score < FRAUD_CLEAR:
        return CRSResult("verified", [f"owner identity verified, fraud risk {score} of 100"], evidence)
    return CRSResult("unknown", ["the owner's identity didn't fully verify"
                                 + (f" ({', '.join(sorted(codes))})" if codes else "")], evidence)


def _post(http, path: str, body: dict, token: str) -> dict:
    try:
        resp = http.post(f"{_base()}{path}", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_S)
    except requests.exceptions.RequestException as e:
        raise CRSError(f"CRS request did not complete: {e}") from e
    if resp.status_code in (401, 403):
        raise CRSError(f"CRS answered HTTP {resp.status_code} to {path}; the product may not be enabled for the account")
    if resp.status_code >= 400:
        raise CRSError(f"CRS answered HTTP {resp.status_code} to {path}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as e:
        raise CRSError(f"CRS answered {path} with something other than JSON") from e


def verify_principal(p: dict, http=requests) -> CRSResult:
    token = _login(http)
    identity = _post(http, "/transunion/identity-manager-verification/basic", {
        "firstName": p["first_name"], "lastName": p["last_name"], "ssn": str(p["ssn"]), "dob": str(p["dob"]),
        "street1": p["street"], "city": p["city"], "state": p["state"], "zip": str(p["zip"])}, token)
    fraud = _post(http, "/fraud-finder/fraud-finder", {
        "firstName": p["first_name"], "lastName": p["last_name"], "phoneNumber": str(p["phone"]), "email": p["email"],
        "address": {"addressLine1": p["street"], "city": p["city"], "state": p["state"], "postalCode": str(p["zip"])}},
        token)
    return interpret_principal(identity, fraud)


def check_vendor(vendor: Vendor, http=requests) -> tuple[Vendor, CRSResult | None, str | None]:
    """(vendor with crs_status from CRS, the result, error). If the check itself fails, the vendor is
    returned unchanged: an outage shouldn't overwrite a status CRS already gave."""
    principal = principal_for(vendor.id)
    try:
        result = verify_principal(principal, http=http) if principal else \
            verify_business(vendor.legal_name, vendor.ein, http=http)
    except CRSError as e:
        return vendor, None, str(e)
    return vendor.model_copy(update={"crs_status": result.status}), result, None
