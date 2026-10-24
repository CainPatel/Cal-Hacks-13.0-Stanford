"""The only module allowed to call Nessie (CLAUDE.md hard rule #2). A payment executes
only if every RuleResult passed AND (amount <= auto_release_limit OR a valid ESP32
signature is present) -- hard rule #3. Rules 14/15 failing (over the limit, unsure
judgment) is what escalation means, so they may fail only when a valid signature is present;
every escalation needs one, whatever the amount. The signature must cover this request's
exact payee and amount, and each one pays once. This module does the I/O the rest of kernel/ is
forbidden from doing: it is the trust boundary between a Decision and money moving.
"""
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from events import log as events
from kernel.rules import ALL_RULES
from kernel.runner import ESCALATION_RULES
from shared.schema import Action, Decision, LedgerEntry, LedgerKind, PayRequest

# Capital One's Nessie hackathon sandbox: a pretend bank (no real money, no real accounts).
# The GC's operating account lives there; owner draws are deposits into it, sub payments are bills
# paid from it. Host per Nessie's OpenAPI spec; override with NESSIE_BASE_URL.
NESSIE_BASE_URL = "https://prod-api.nessieisreal.com"

EXPECTED_RULES = {sys.modules[r.__module__].RULE for r in ALL_RULES}  # a Decision must carry all of them


class ExecutionRefused(Exception):
    """Raised instead of ever silently skipping a payment. Callers must not catch this
    to retry with weaker checks -- it means the gate did its job."""


class NessieError(Exception):
    """A Nessie call failed. `definite` is True only when the bank certainly did nothing
    (it answered with an error, or the connection never opened); a timeout or a dropped
    connection mid-request is not definite, because the payment may have gone through."""

    def __init__(self, message: str, definite: bool):
        super().__init__(_scrub(message))
        self.definite = definite


def _scrub(text: str) -> str:
    """Nessie takes its key in the query string, and HTTP libraries put URLs in their error messages."""
    return re.sub(r"(key=)[^&\s'\")]+", r"\1<hidden>", str(text))


@dataclass(frozen=True)
class SignedApproval:
    """What the ESP32 returns from POST /approve."""
    payload: dict      # the exact dict sent to the device
    sig_hex: str        # hex(r || s), ECDSA-P256 over SHA-256(canonical body bytes)
    nonce: str
    rejected: bool = False


def canonical_payload_bytes(payload: dict) -> bytes:
    """Must match firmware/approval_key/approval_key.ino exactly: the device signs the
    exact bytes it received, over this same sort_keys/compact-separator encoding."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def verify_signature(body: bytes, sig_hex: str, pubkey_hex: str, rejected: bool = False) -> bool:
    """True iff sig_hex is a valid ECDSA-P256/SHA-256 signature over body (or "REJECT"+body
    for a rejection) under the registered device public key."""
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(pubkey_hex))
    sig = bytes.fromhex(sig_hex)
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    signed = (b"REJECT" + body) if rejected else body
    try:
        pub.verify(der, signed, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def request_device_approval(device_url: str, payload: dict, timeout: float = 100.0) -> SignedApproval:
    """POSTs the payment payload to the ESP32 and waits for a button press. This is the
    only place the server talks to the device; the device never talks to Nessie."""
    body = canonical_payload_bytes(payload)
    resp = requests.post(f"{device_url}/approve", data=body,
                          headers={"Content-Type": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return SignedApproval(payload=payload, sig_hex=data["sig"], nonce=payload["nonce"],
                           rejected=bool(data.get("rejected", False)))


def _check_signature_gate(request: PayRequest, amount: Decimal, approval: SignedApproval, pubkey_hex: str,
                           used_nonces: set[str]) -> None:
    if approval.nonce in used_nonces or approval.payload.get("nonce") != approval.nonce:
        raise ExecutionRefused(f"nonce {approval.nonce} already used; signature replay rejected")
    if approval.payload.get("expires_at", 0) < time.time():
        raise ExecutionRefused("signature expired")
    if approval.payload.get("request_id") != request.id:
        raise ExecutionRefused("signature is for a different request_id")
    # the owner approved what the device showed: that must be exactly what we pay, to whom
    try:
        signed_amount = Decimal(str(approval.payload.get("amount")))
    except ArithmeticError:
        raise ExecutionRefused("signed amount is not a number")
    if signed_amount != amount:
        raise ExecutionRefused(f"signed amount {signed_amount} is not the amount being paid ({amount})")
    if approval.payload.get("payee") != request.sub_id:
        raise ExecutionRefused("signature is for a different payee")
    if "bank_account" in approval.payload and approval.payload["bank_account"] != request.bank_account:
        raise ExecutionRefused("signature approved payment to a different bank account")
    body = canonical_payload_bytes(approval.payload)
    if not verify_signature(body, approval.sig_hex, pubkey_hex, rejected=approval.rejected):
        raise ExecutionRefused("signature does not verify against the registered device pubkey")
    if approval.rejected:
        raise ExecutionRefused("a human rejected this payment on the device")


# One payment at a time: the paid-once check, the bank call and the record happen under this lock,
# so two simultaneous /pay calls can't both see "not paid" (single API process; see README).
_PAY_LOCK = threading.Lock()


def payment_status(request_id: str, log_path=events.DEFAULT_PATH) -> str | None:
    """'paid', 'unknown' (a payment was attempted and the bank's answer is unknown), or None (payable)."""
    status = None
    for e in events.read(kind="execution", request_id=request_id, path=log_path):
        s = e.data.get("status", "paid")
        if s == "paid":
            return "paid"
        if s == "started":
            status = "unknown"  # cleared below only by a definite failure or a resolution
        elif s == "failed":
            status = None if e.data.get("definite") else "unknown"
        elif s == "resolved_unpaid":
            status = None
    return status


def check_payable(request: PayRequest, log_path=events.DEFAULT_PATH) -> None:
    """Refuse anything that is, or might already be, paid: this request, or the same application again."""
    status = payment_status(request.id, log_path)
    if status == "paid":
        raise ExecutionRefused(f"{request.id} has already been paid")
    if status == "unknown":
        raise ExecutionRefused(f"an earlier attempt to pay {request.id} has an unknown outcome; resolve it against "
                               "the bank first (POST /requests/{id}/resolve or `python -m events nessie-resolve`)")
    state = events.state(path=log_path)
    numbers = {r.id: r.number for r in state["pay_requests"]}
    for e in state["ledger"]:
        if e.kind != LedgerKind.sub_payment:
            continue
        if e.request_id == request.id:
            raise ExecutionRefused(f"{request.id} has already been paid (the bank ledger shows the payment)")
        if e.sub_id == request.sub_id and e.job_id == request.job_id and (
                e.period_end == request.period_end or numbers.get(e.request_id) == request.number):
            raise ExecutionRefused(f"{request.sub_id} was already paid on {request.job_id} for this period or "
                                   f"application number (request {e.request_id})")


def execute(decision: Decision, request: PayRequest, *, auto_release_limit_usd: Decimal,
            pubkey_hex: str | None = None, approval: SignedApproval | None = None,
            used_nonces: set[str] | None = None, log_path=events.DEFAULT_PATH) -> dict:
    """The single choke point money passes through. Raises ExecutionRefused and does not
    call Nessie unless every condition in hard rule #3 holds.
    """
    if decision.request_id != request.id:
        raise ExecutionRefused(f"decision is for {decision.request_id}, not {request.id}")
    if decision.action not in (Action.release, Action.escalate):
        raise ExecutionRefused(f"decision is {decision.action}, not release/escalate")
    present = {r.rule for r in decision.results}
    if not EXPECTED_RULES <= present:
        raise ExecutionRefused(f"decision is missing rules: {sorted(EXPECTED_RULES - present)}")
    failed = {r.rule for r in decision.results if not r.passed}
    if failed - ESCALATION_RULES:
        raise ExecutionRefused(f"not every rule passed: {sorted(failed - ESCALATION_RULES)}")

    g = request.g702
    lines_due = sum(l.total_to_date for l in request.lines) - sum(l.retainage for l in request.lines) \
        - g.less_previous_certificates
    if g.current_payment_due != lines_due:  # rule 01 checks the ledger; this guards the number we pay
        raise ExecutionRefused(f"payment due {g.current_payment_due} doesn't match the lines ({lines_due})")
    amount = g.current_payment_due

    with _PAY_LOCK:
        check_payable(request, log_path)
        executions = events.read(kind="execution", path=log_path)
        needs_signature = decision.action == Action.escalate or bool(failed) or amount > Decimal(str(auto_release_limit_usd))
        if needs_signature:
            if approval is None or pubkey_hex is None:
                raise ExecutionRefused("this payment needs the owner's signed approval and none was given")
            released = {e.data.get("released_nonce") for e in executions if e.data.get("status") == "failed"}
            used = (set(used_nonces or ()) | {e.data.get("nonce") for e in events.read(kind="signature", path=log_path)}) \
                - released
            _check_signature_gate(request, amount, approval, pubkey_hex, used)
            events.append("signature", {"sig": approval.sig_hex, "nonce": approval.nonce,
                                         "payload": approval.payload}, request_id=request.id, path=log_path)

        # Written before the bank call: if the process dies mid-payment, the request stays blocked
        # until someone checks the bank, instead of being paid again.
        events.append("execution", {"status": "started", "amount": str(amount),
                                     "nonce": approval.nonce if approval else None},
                      request_id=request.id, path=log_path)
        try:
            result = _nessie_pay(request, amount)
        except NessieError as e:
            # A definite failure moved no money, so the owner's approval may be used again; anything
            # else might have paid, so the approval stays spent and the request waits for a resolution.
            events.append("execution", {"status": "failed", "definite": e.definite, "error": str(e),
                                         "released_nonce": approval.nonce if (e.definite and approval) else None},
                          request_id=request.id, path=log_path)
            raise ExecutionRefused(f"the bank didn't take the payment: {e}" if e.definite else
                                   f"the payment's outcome is unknown ({e}); resolve it against the bank before "
                                   "trying again") from e
        _record_payment(request, amount, result, log_path)
        return result


def _record_payment(request: PayRequest, amount: Decimal, result: dict, log_path) -> None:
    events.append("execution", result, request_id=request.id, path=log_path)
    events.append("fact", LedgerEntry(
        id=f"nessie_{result['nessie_bill_id']}" if result.get("nessie_bill_id") else f"pay_{request.id}",
        job_id=request.job_id, kind=LedgerKind.sub_payment, amount=amount, date=date.today(),
        period_end=request.period_end, sub_id=request.sub_id, request_id=request.id,
        bank_account=request.bank_account), table="ledger", path=log_path)


def resolve_unknown(request: PayRequest, log_path=events.DEFAULT_PATH) -> str:
    """After an unknown outcome, ask the bank: a bill tagged with this request means it was paid
    (recorded now); none means it wasn't, and the request may be paid again. Returns 'paid' or 'unpaid'."""
    with _PAY_LOCK:
        if payment_status(request.id, log_path) != "unknown":
            raise ExecutionRefused(f"{request.id} has no payment with an unknown outcome to resolve")
        base, key, account = _nessie_config()
        bills = [b for b in _nessie("GET", f"/accounts/{account}/bills", key=key, base=base) or []
                 if isinstance(b, dict) and str(b.get("nickname", "")).startswith(f"pay|{request.id}|")]
        if bills:
            b = bills[0]
            amount = Decimal(str(b.get("payment_amount")))
            _record_payment(request, amount, {"status": "paid", "request_id": request.id, "amount": str(amount),
                                              "bank": "nessie", "nessie_bill_id": b.get("_id"), "resolved": True,
                                              "duplicate_bills": len(bills) - 1}, log_path)
            return "paid"
        events.append("execution", {"status": "resolved_unpaid", "checked": "no bill for this request in Nessie"},
                      request_id=request.id, path=log_path)
        return "unpaid"


def _nessie_config() -> tuple[str, str, str]:
    """(base URL, API key, the GC's account id). Missing settings are a definite failure: nothing was sent."""
    key, account = os.environ.get("NESSIE_API_KEY"), os.environ.get("NESSIE_ACCOUNT_ID")
    if not key:
        raise NessieError("NESSIE_API_KEY is not set; refusing to simulate a payment", definite=True)
    if not account:
        raise NessieError("NESSIE_ACCOUNT_ID is not set; run `python -m events nessie-setup` once", definite=True)
    return os.environ.get("NESSIE_BASE_URL", NESSIE_BASE_URL).rstrip("/"), key, account


def _nessie(method: str, path: str, *, key: str, base: str, body: dict | None = None, timeout: float = 15):
    try:
        resp = requests.request(method, f"{base}{path}", params={"key": key}, json=body, timeout=timeout)
    except requests.exceptions.ConnectTimeout as e:
        raise NessieError(f"could not connect to Nessie: {e}", definite=True) from e
    except requests.exceptions.RequestException as e:
        raise NessieError(f"Nessie request did not complete: {e}", definite=False) from e
    if resp.status_code >= 400:
        # A 4xx means Nessie refused the request. A 5xx (or 408) can come from a gateway after the
        # bill was already written, so it doesn't prove the payment failed.
        definite = 400 <= resp.status_code < 500 and resp.status_code != 408
        raise NessieError(f"Nessie answered HTTP {resp.status_code}: {resp.text[:300]}", definite=definite)
    try:
        return resp.json()
    except ValueError:
        return {"message": resp.text[:300]}


def _created_id(body) -> str | None:
    """Nessie has answered creates both as {code, message, objectCreated: {_id}} and as the object itself."""
    if isinstance(body, dict):
        obj = body.get("objectCreated") if isinstance(body.get("objectCreated"), dict) else body
        return obj.get("_id") or obj.get("id")
    return None


def _payment_tag(request: PayRequest) -> str:
    # Everything rule 01/10 need from a past payment, kept on the bill so the ledger can be rebuilt from Nessie.
    return "|".join(["pay", request.id, request.sub_id, request.job_id, request.period_end.isoformat(),
                     request.bank_account])


def _nessie_pay(request: PayRequest, amount: Decimal) -> dict:
    """Pay the sub: a completed bill on the GC's Nessie account."""
    base, key, account = _nessie_config()
    bill = {"status": "completed", "payee": request.sub_id, "nickname": _payment_tag(request),
            "payment_date": date.today().isoformat(), "payment_amount": float(amount)}
    body = _nessie("POST", f"/accounts/{account}/bills", key=key, base=base, body=bill)
    return {"status": "paid", "request_id": request.id, "amount": str(amount), "bank": "nessie",
            "nessie_bill_id": _created_id(body), "nessie_response": body}


# --- the GC's ledger in Nessie (rule 08's owner draws, and past sub payments) -----------------


def nessie_ledger() -> tuple[list[LedgerEntry], list[str]]:
    """(ledger facts, skipped rows) from the GC's Nessie account: owner draws (deposits) and sub payments
    (bills). Only rows this system wrote (tagged) are read; a malformed tagged row is skipped and named."""
    base, key, account = _nessie_config()
    out, skipped = [], []
    for d in _nessie("GET", f"/accounts/{account}/deposits", key=key, base=base) or []:
        parts = str(d.get("description", "")).split("|") if isinstance(d, dict) else []
        if len(parts) != 4 or parts[0] != "draw" or d.get("status", "completed") == "cancelled":
            continue
        try:
            _, job_id, period_end, amount = parts
            exact = Decimal(amount)
            if abs(exact - Decimal(str(d.get("amount", 0)))) > 1:  # the tag may not claim more than the bank holds
                raise ValueError(f"tag says {exact}, deposit is {d.get('amount')}")
            out.append(LedgerEntry(id=f"nessie_{d['_id']}", job_id=job_id, kind=LedgerKind.owner_draw, amount=exact,
                                   date=date.fromisoformat(str(d["transaction_date"])[:10]),
                                   period_end=date.fromisoformat(period_end)))
        except (ValueError, KeyError, ArithmeticError) as e:
            skipped.append(f"deposit {d.get('_id')}: {e}")
    for b in _nessie("GET", f"/accounts/{account}/bills", key=key, base=base) or []:
        parts = str(b.get("nickname", "")).split("|") if isinstance(b, dict) else []
        if len(parts) < 6 or parts[0] != "pay" or b.get("status") not in ("completed", "pending"):
            continue
        try:
            _, request_id, sub_id, job_id, period_end = parts[:5]
            bank_account = "|".join(parts[5:]) or None
            out.append(LedgerEntry(id=f"nessie_{b['_id']}", job_id=job_id, kind=LedgerKind.sub_payment,
                                   amount=Decimal(str(b["payment_amount"])),
                                   date=date.fromisoformat(str(b.get("payment_date") or b.get("creation_date"))[:10]),
                                   period_end=date.fromisoformat(period_end), sub_id=sub_id,
                                   request_id=request_id, bank_account=bank_account))
        except (ValueError, KeyError, ArithmeticError) as e:
            skipped.append(f"bill {b.get('_id')}: {e}")
    return out, skipped


def nessie_setup(ledger: list[LedgerEntry], gc_name: tuple[str, str] = ("Ridgeline", "Builders")) -> str:
    """Create the GC as a Nessie customer with a checking account and write its history into it:
    owner draws as deposits, past sub payments as completed bills. Returns the new account id.
    Needs only NESSIE_API_KEY (the account doesn't exist yet)."""
    key = os.environ.get("NESSIE_API_KEY")
    if not key:
        raise NessieError("NESSIE_API_KEY is not set", definite=True)
    base = os.environ.get("NESSIE_BASE_URL", NESSIE_BASE_URL).rstrip("/")
    first, last = gc_name
    customer = _created_id(_nessie("POST", "/customers", key=key, base=base, body={
        "first_name": first, "last_name": last,
        "address": {"street_number": "100", "street_name": "Harbor Way", "city": "Oakland", "state": "CA",
                    "zip": "94607"}}))
    if not customer:  # some Nessie builds answer creates with a bare message; find what we just made
        matches = [c for c in _nessie("GET", "/customers", key=key, base=base) or []
                   if c.get("first_name") == first and c.get("last_name") == last]
        customer = matches[-1]["_id"] if matches else None
    if not customer:
        raise NessieError("created the GC customer but Nessie returned no id", definite=False)
    account = _created_id(_nessie("POST", f"/customers/{customer}/accounts", key=key, base=base, body={
        "type": "Checking", "nickname": f"{first} {last} operating", "rewards": 0, "balance": 0}))
    if not account:
        accounts = _nessie("GET", f"/customers/{customer}/accounts", key=key, base=base) or []
        account = accounts[-1]["_id"] if accounts else None
    if not account:
        raise NessieError("created the GC account but Nessie returned no id", definite=False)
    for e in sorted(ledger, key=lambda e: e.date):
        if e.kind == LedgerKind.owner_draw:
            _nessie("POST", f"/accounts/{account}/deposits", key=key, base=base, body={
                "medium": "balance", "transaction_date": e.date.isoformat(), "status": "completed",
                "amount": int(e.amount.to_integral_value()),  # Nessie deposits are whole dollars; exact amount in the tag
                "description": f"draw|{e.job_id}|{e.period_end.isoformat()}|{e.amount}"})
        elif e.request_id and e.sub_id:
            _nessie("POST", f"/accounts/{account}/bills", key=key, base=base, body={
                "status": "completed", "payee": e.sub_id, "payment_date": e.date.isoformat(),
                "payment_amount": float(e.amount),
                "nickname": "|".join(["pay", e.request_id, e.sub_id, e.job_id, e.period_end.isoformat(),
                                      e.bank_account or ""])})
    return account
