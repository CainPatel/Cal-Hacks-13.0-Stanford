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
import sys
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
        super().__init__(message)
        self.definite = definite


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
    body = canonical_payload_bytes(approval.payload)
    if not verify_signature(body, approval.sig_hex, pubkey_hex, rejected=approval.rejected):
        raise ExecutionRefused("signature does not verify against the registered device pubkey")
    if approval.rejected:
        raise ExecutionRefused("a human rejected this payment on the device")


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

    executions = events.read(kind="execution", path=log_path)
    if any(e.request_id == request.id and e.data.get("status", "paid") == "paid" for e in executions):
        raise ExecutionRefused(f"{request.id} has already been paid")
    if any(e.kind == LedgerKind.sub_payment and e.request_id == request.id
           for e in events.state(path=log_path)["ledger"]):
        raise ExecutionRefused(f"{request.id} has already been paid (the bank ledger shows the payment)")
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

    try:
        result = _nessie_pay(request, amount)
    except NessieError as e:
        # A definite failure moved no money, so the owner's approval may be used again; anything
        # else might have paid, so the approval stays spent and a person checks the bank first.
        events.append("execution", {"status": "failed", "definite": e.definite, "error": str(e),
                                     "released_nonce": approval.nonce if (e.definite and approval) else None},
                      request_id=request.id, path=log_path)
        raise ExecutionRefused(f"the bank didn't take the payment: {e}" if e.definite else
                               f"the payment's outcome is unknown ({e}); check Nessie before trying again") from e
    events.append("execution", result, request_id=request.id, path=log_path)
    events.append("fact", LedgerEntry(
        id=f"nessie_{result['nessie_bill_id']}" if result.get("nessie_bill_id") else f"pay_{request.id}",
        job_id=request.job_id, kind=LedgerKind.sub_payment, amount=amount, date=date.today(),
        period_end=request.period_end, sub_id=request.sub_id, request_id=request.id,
        bank_account=request.bank_account), table="ledger", path=log_path)
    return result


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
        raise NessieError(f"Nessie answered HTTP {resp.status_code}: {resp.text[:300]}", definite=True)
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


def nessie_ledger() -> list[LedgerEntry]:
    """Owner draws (deposits) and sub payments (bills) on the GC's Nessie account, as ledger facts.
    Only entries this system wrote (tagged descriptions) are read; anything else in the sandbox is ignored."""
    base, key, account = _nessie_config()
    out = []
    for d in _nessie("GET", f"/accounts/{account}/deposits", key=key, base=base) or []:
        parts = str(d.get("description", "")).split("|")
        if len(parts) == 4 and parts[0] == "draw" and d.get("status", "completed") == "completed":
            _, job_id, period_end, amount = parts
            out.append(LedgerEntry(id=f"nessie_{d['_id']}", job_id=job_id, kind=LedgerKind.owner_draw,
                                   amount=Decimal(amount), date=date.fromisoformat(d["transaction_date"][:10]),
                                   period_end=date.fromisoformat(period_end)))
    for b in _nessie("GET", f"/accounts/{account}/bills", key=key, base=base) or []:
        parts = str(b.get("nickname", "")).split("|")
        if len(parts) == 6 and parts[0] == "pay" and b.get("status") == "completed":
            _, request_id, sub_id, job_id, period_end, bank_account = parts
            out.append(LedgerEntry(id=f"nessie_{b['_id']}", job_id=job_id, kind=LedgerKind.sub_payment,
                                   amount=Decimal(str(b["payment_amount"])),
                                   date=date.fromisoformat(str(b.get("payment_date") or b.get("creation_date"))[:10]),
                                   period_end=date.fromisoformat(period_end), sub_id=sub_id,
                                   request_id=request_id, bank_account=bank_account))
    return out


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
