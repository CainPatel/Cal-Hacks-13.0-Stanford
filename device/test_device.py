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
    other = json.dumps({**payload, "nonce": secrets.token_hex(8)}, sort_keys=True, separators=(",", ":")).encode()
    assert client.post("/approve", content=other).status_code == 409  # one payment at a time
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
    assert 'go(\'approve\')' in dev[1].get("/").text
    _, _, resp = run(dev, lambda main: dev[1].post("/decide/approve"))
    assert "rejected" not in resp


def test_buttons_only_refuses_page_and_takes_gpio(dev, monkeypatch):
    main, client = dev
    monkeypatch.setattr(main, "BUTTONS_ONLY", True)
    page = client.get("/").text
    assert "Press the green or red button on the key." in page and "<button" not in page

    def press(main):
        for what in ("approve", "reject"):
            assert client.post(f"/decide/{what}").status_code == 403
        assert client.get("/pending").json()  # still waiting: the page decided nothing
        physical("approve_btn")(main)

    _, payload, resp = run(dev, press)
    assert resp == {"sig": resp["sig"], "nonce": payload["nonce"]}


def test_bad_and_expired_payloads(dev):
    client = dev[1]
    assert client.post("/approve", content=b"not json").status_code == 400
    old = {"request_id": "x", "payee": "p", "amount": "1", "job": "j", "nonce": "n", "expires_at": 1}
    assert client.post("/approve", json=old).status_code == 400


def test_private_key_is_private(dev):
    path = os.path.join(os.environ["HOME"], ".drawkey", "private.pem")
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert "PRIVATE" not in dev[1].get("/pubkey", params={"format": "pem"}).text


def payment(**over):
    p = {"request_id": "test_round", "payee": "Bayline Steel Co.", "amount": "15644.70", "job": "Harbor Point",
         "nonce": secrets.token_hex(8), "expires_at": int(time.time()) + 90, **over}
    return p, json.dumps(p, sort_keys=True, separators=(",", ":")).encode()


def test_waits_in_rounds_and_a_press_between_rounds_still_counts(dev, monkeypatch):
    main, client = dev
    monkeypatch.setattr(main, "HOLD", 0.2)
    p, body = payment()
    assert client.post("/approve", content=body).status_code == 202  # still waiting: ask again
    physical("approve_btn")(main)  # pressed while no request was open
    r = client.post("/approve", content=body)
    assert r.status_code == 200 and r.json()["nonce"] == p["nonce"] and "sig" in r.json()


def test_a_lost_answer_comes_back_the_same_on_resend(dev, monkeypatch):
    main, client = dev
    monkeypatch.setattr(main, "HOLD", 0.2)
    _, body = payment()
    client.post("/approve", content=body)
    physical("approve_btn")(main)
    first = client.post("/approve", content=body).json()
    again = client.post("/approve", content=body)  # the first answer never reached the server
    assert again.status_code == 200 and again.json() == first
    assert client.get("/pending").json() == {}


def test_same_nonce_with_different_bytes_is_refused(dev, monkeypatch):
    main, client = dev
    monkeypatch.setattr(main, "HOLD", 0.2)
    p, body = payment()
    assert client.post("/approve", content=body).status_code == 202
    _, changed = payment(nonce=p["nonce"], amount="99999.00")
    assert client.post("/approve", content=changed).status_code == 409  # never re-sign other contents
    physical("reject_btn")(main)
    assert client.post("/approve", content=body).json()["rejected"] is True


def test_an_abandoned_wait_frees_the_key(dev, monkeypatch):
    main, client = dev
    monkeypatch.setattr(main, "HOLD", 0.1)
    monkeypatch.setattr(main, "STALE", 0.3)
    _, abandoned = payment()
    assert client.post("/approve", content=abandoned).status_code == 202
    _, nxt = payment()
    assert client.post("/approve", content=nxt).status_code == 409  # still asked about recently
    time.sleep(0.4)  # the server stopped asking about the first one
    assert client.post("/approve", content=nxt).status_code == 202
    physical("approve_btn")(main)
    assert "sig" in client.post("/approve", content=nxt).json()


def test_times_out_when_nobody_presses(dev, monkeypatch):
    main, client = dev
    monkeypatch.setattr(main, "HOLD", 0.2)
    monkeypatch.setitem(main.CFG, "wait_seconds", 0.3)
    _, body = payment()
    codes = [client.post("/approve", content=body).status_code for _ in range(3)]
    assert codes[-1] == 408 and client.get("/pending").json() == {}
