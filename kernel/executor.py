"""The only module allowed to call Nessie (CLAUDE.md hard rule #2). A payment executes
only if every RuleResult passed AND (amount <= auto_release_limit OR a valid ESP32
signature is present) -- hard rule #3. This module does the I/O the rest of kernel/ is
forbidden from doing: it is the trust boundary between a Decision and money moving.
"""
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from events import log as events
from shared.schema import Action, Decision, PayRequest


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


def _check_signature_gate(request: PayRequest, approval: SignedApproval, pubkey_hex: str,
                           used_nonces: set[str]) -> None:
    if approval.nonce in used_nonces:
        raise ExecutionRefused(f"nonce {approval.nonce} already used; signature replay rejected")
    if approval.payload.get("expires_at", 0) < time.time():
        raise ExecutionRefused("signature expired")
    if approval.payload.get("request_id") != request.id:
        raise ExecutionRefused("signature is for a different request_id")
    body = canonical_payload_bytes(approval.payload)
    if not verify_signature(body, approval.sig_hex, pubkey_hex, rejected=approval.rejected):
        raise ExecutionRefused("signature does not verify against the registered device pubkey")
    if approval.rejected:
        raise ExecutionRefused("a human rejected this payment on the device")


def execute(decision: Decision, request: PayRequest, *, auto_release_limit_usd: Decimal,
            pubkey_hex: str | None = None, approval: SignedApproval | None = None,
            used_nonces: set[str] | None = None) -> dict:
    """The single choke point money passes through. Raises ExecutionRefused and does not
    call Nessie unless every condition in hard rule #3 holds.
    """
    if decision.action != Action.release and decision.action != Action.escalate:
        raise ExecutionRefused(f"decision is {decision.action}, not release/escalate")
    if not all(r.passed for r in decision.results):
        raise ExecutionRefused("not every rule passed")

    amount = request.g702.current_payment_due
    if amount > auto_release_limit_usd:
        if approval is None or pubkey_hex is None:
            raise ExecutionRefused("amount exceeds auto-release limit and no signed approval was given")
        _check_signature_gate(request, approval, pubkey_hex, used_nonces or set())
        events.append("signature", {"sig": approval.sig_hex, "nonce": approval.nonce,
                                     "payload": approval.payload}, request_id=request.id)

    result = _nessie_pay(request, amount)
    events.append("execution", result, request_id=request.id)
    return result


def _nessie_pay(request: PayRequest, amount: Decimal) -> dict:
    """The actual Nessie sandbox call. Capital One's Nessie API is the simulated bank in
    this project (CLAUDE.md); only this function touches it.
    """
    api_key = os.environ.get("NESSIE_API_KEY")
    if not api_key:
        raise ExecutionRefused("NESSIE_API_KEY is not set; refusing to simulate a payment")
    resp = requests.post(
        "http://api.nessieisreal.com/payments",
        params={"key": api_key},
        json={"payee_id": request.sub_id, "amount": str(amount),
              "description": f"Pay request {request.id}"},
        timeout=10,
    )
    resp.raise_for_status()
    return {"request_id": request.id, "amount": str(amount), "nessie_response": resp.json()}
