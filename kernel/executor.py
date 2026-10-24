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
from decimal import Decimal

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from events import log as events
from kernel.rules import ALL_RULES
from kernel.runner import ESCALATION_RULES
from shared.schema import Action, Decision, PayRequest

EXPECTED_RULES = {sys.modules[r.__module__].RULE for r in ALL_RULES}  # a Decision must carry all of them


class ExecutionRefused(Exception):
    """Raised instead of ever silently skipping a payment. Callers must not catch this
    to retry with weaker checks -- it means the gate did its job."""


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

    needs_signature = decision.action == Action.escalate or bool(failed) or amount > Decimal(str(auto_release_limit_usd))
    if needs_signature:
        if approval is None or pubkey_hex is None:
            raise ExecutionRefused("this payment needs the owner's signed approval and none was given")
        used = set(used_nonces or ()) | {e.data.get("nonce") for e in events.read(kind="signature", path=log_path)}
        _check_signature_gate(request, amount, approval, pubkey_hex, used)
        events.append("signature", {"sig": approval.sig_hex, "nonce": approval.nonce,
                                     "payload": approval.payload}, request_id=request.id, path=log_path)

    result = _nessie_pay(request, amount)
    events.append("execution", result, request_id=request.id, path=log_path)
    return result


def _nessie_pay(request: PayRequest, amount: Decimal) -> dict:
    """The actual Nessie sandbox call. Capital One's Nessie API is the simulated bank in
    this project (CLAUDE.md); only this function touches it.
    """
    api_key = os.environ.get("NESSIE_API_KEY")
    if not api_key:
        raise ExecutionRefused("NESSIE_API_KEY is not set; refusing to simulate a payment")
    resp = requests.post(
        "https://api.nessieisreal.com/payments",  # TLS: the key is in the query string
        params={"key": api_key},
        json={"payee_id": request.sub_id, "amount": str(amount),
              "description": f"Pay request {request.id}"},
        timeout=10,
    )
    resp.raise_for_status()
    return {"request_id": request.id, "amount": str(amount), "nessie_response": resp.json()}
