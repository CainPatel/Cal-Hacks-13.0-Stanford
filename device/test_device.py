"""Send a fake payment to the device in mock mode, press a button, verify the signature like the executor will.

    make device-test
"""
import json
import os
import secrets
import threading
import time

import pytest

os.environ["DRAWKEY_MOCK"] = "1"


@pytest.fixture(scope="module")
def dev(tmp_path_factory):
    os.environ["HOME"] = str(tmp_path_factory.mktemp("home"))  # fresh key, never touches ~/.drawkey
    from fastapi.testclient import TestClient
    from device import main
    return main, TestClient(main.app)


def run(dev, press):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    main, client = dev
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(client.get("/pubkey").text))
    payload = {"request_id": "test_1", "payee": "Summit Drywall LLC", "amount": "38000.00",
               "job": "Harbor Point Medical Office", "nonce": secrets.token_hex(8),
               "expires_at": int(time.time()) + 90}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()  # canonical JSON

    out = {}
    t = threading.Thread(target=lambda: out.update(r=client.post("/approve", content=body)))
    t.start()
    while not client.get("/pending").json():
        time.sleep(0.01)
    assert client.post("/approve", content=body).status_code == 409  # one at a time
    press(main)
    t.join(5)
    resp = out["r"].json()

    sig = bytes.fromhex(resp["sig"])
    der = encode_dss_signature(int.from_bytes(sig[:32]), int.from_bytes(sig[32:]))
    signed = b"REJECT" + body if resp.get("rejected") else body
    pub.verify(der, signed, ec.ECDSA(hashes.SHA256()))  # raises InvalidSignature if wrong
    return main, payload, resp


def physical(btn):
    def press(main):
        pin = getattr(main, btn).pin
        pin.drive_low(); time.sleep(0.05); pin.drive_high()
    return press


def test_approve_physical_button(dev):
    main, payload, resp = run(dev, physical("approve_btn"))
    assert resp == {"sig": resp["sig"], "nonce": payload["nonce"]}
    assert main.green.is_lit and not main.red.is_lit


def test_reject_physical_button(dev):
    main, _, resp = run(dev, physical("reject_btn"))
    assert resp["rejected"] is True
    assert main.red.is_lit and not main.green.is_lit


def test_approve_page_button(dev):
    _, _, resp = run(dev, lambda main: dev[1].post("/decide/approve"))
    assert "rejected" not in resp


def test_bad_and_expired_payloads(dev):
    client = dev[1]
    assert client.post("/approve", content=b"not json").status_code == 400
    old = {"request_id": "x", "payee": "p", "amount": "1", "job": "j", "nonce": "n", "expires_at": 1}
    assert client.post("/approve", json=old).status_code == 400


def test_private_key_is_private(dev):
    path = os.path.join(os.environ["HOME"], ".drawkey", "private.pem")
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert "PRIVATE" not in dev[1].get("/pubkey", params={"format": "pem"}).text
