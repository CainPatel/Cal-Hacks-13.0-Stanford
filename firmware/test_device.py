# /// script
# dependencies = ["cryptography"]
# ///
"""Send a fake payment to the device and check its signature, the same way the executor will.

    uv run firmware/test_device.py <device-ip>
"""
import json
import secrets
import sys
import time
import urllib.request

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

ip = sys.argv[1]
pub_hex = urllib.request.urlopen(f"http://{ip}/pubkey", timeout=5).read().decode().strip()
pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(pub_hex))
print("pubkey", pub_hex)

payload = {"request_id": "test_1", "payee": "Summit Drywall LLC", "amount": "38000.00",
           "job": "Harbor Point Medical Office", "nonce": secrets.token_hex(8),
           "expires_at": int(time.time()) + 90}
body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()  # canonical JSON
print(f"Press a button on the device (page: http://{ip}:8080) ...")
req = urllib.request.Request(f"http://{ip}/approve", body, {"Content-Type": "application/json"})
resp = json.load(urllib.request.urlopen(req, timeout=100))

sig = bytes.fromhex(resp["sig"])
der = encode_dss_signature(int.from_bytes(sig[:32]), int.from_bytes(sig[32:]))
signed = b"REJECT" + body if resp.get("rejected") else body
pub.verify(der, signed, ec.ECDSA(hashes.SHA256()))  # raises InvalidSignature if wrong
if not resp.get("rejected"):
    assert resp["nonce"] == payload["nonce"], resp
print("REJECTED, signature valid" if resp.get("rejected") else "APPROVED, signature valid")
