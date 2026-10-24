"""Signature-gate tests for kernel/executor.py, using a software P-256 key standing in
for the ESP32 (firmware/check_device.py exercises the real device the same way).
"""
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from kernel.executor import ExecutionRefused, SignedApproval, canonical_payload_bytes, verify_signature


def make_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return priv, pub_bytes.hex()


def sign(priv, body: bytes) -> str:
    der = priv.sign(body, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex()


@pytest.fixture
def keypair():
    return make_keypair()


def payload(**overrides):
    base = {"request_id": "pr_1", "payee": "Summit Drywall LLC", "amount": "38000.00",
            "job": "job_1", "nonce": "abc123", "expires_at": int(time.time()) + 90}
    return {**base, **overrides}


def test_valid_signature_verifies(keypair):
    priv, pub_hex = keypair
    body = canonical_payload_bytes(payload())
    sig_hex = sign(priv, body)
    assert verify_signature(body, sig_hex, pub_hex)


def test_wrong_pubkey_fails(keypair):
    priv, _ = keypair
    _, other_pub = make_keypair()
    body = canonical_payload_bytes(payload())
    sig_hex = sign(priv, body)
    assert not verify_signature(body, sig_hex, other_pub)


def test_tampered_payload_fails(keypair):
    priv, pub_hex = keypair
    body = canonical_payload_bytes(payload())
    sig_hex = sign(priv, body)
    tampered = canonical_payload_bytes(payload(amount="99999.00"))
    assert not verify_signature(tampered, sig_hex, pub_hex)


def test_expired_approval_refused(keypair, tmp_path):
    from tests.test_executor_gates import decided, signed
    from kernel.executor import execute

    priv, pub_hex = keypair
    w, d, req = decided("pr_sub_10_6")  # escalated: over the limit
    approval = signed(priv, req, expires_at=int(time.time()) - 1)
    with pytest.raises(ExecutionRefused, match="expired"):
        execute(d, req, auto_release_limit_usd=25000, pubkey_hex=pub_hex, approval=approval, log_path=tmp_path / "e.db")
