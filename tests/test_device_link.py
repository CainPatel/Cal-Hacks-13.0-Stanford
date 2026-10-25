"""kernel/executor.request_device_approval asks the key in rounds, with the same bytes, until it answers."""
import pytest
import requests

from kernel import executor as ex

PAYLOAD = {"request_id": "pr_x", "payee": "Bayline Steel Co.", "amount": "15644.70", "job": "job_1",
           "nonce": "n1", "expires_at": 9999999999}


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


def scripted(monkeypatch, answers):
    sent = []

    def post(url, data, headers, timeout):
        sent.append(data)
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(ex.requests, "post", post)
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    return sent


def test_still_waiting_and_dropped_connections_are_asked_again_with_the_same_bytes(monkeypatch):
    sent = scripted(monkeypatch, [Resp(202, {"waiting": True}), requests.exceptions.ConnectionError("reset"),
                                  requests.exceptions.ReadTimeout("proxy"), Resp(200, {"sig": "ab" * 64, "nonce": "n1"})])
    got = ex.request_device_approval("http://key", PAYLOAD)
    assert got.sig_hex == "ab" * 64 and got.nonce == "n1" and not got.rejected
    assert len(sent) == 4 and len(set(sent)) == 1 and sent[0] == ex.canonical_payload_bytes(PAYLOAD)


@pytest.mark.parametrize("status", [400, 408, 409])
def test_refusals_still_raise(monkeypatch, status):
    scripted(monkeypatch, [Resp(status, {"error": "x"})])
    with pytest.raises(requests.exceptions.HTTPError):
        ex.request_device_approval("http://key", PAYLOAD)


def test_gives_up_at_the_deadline(monkeypatch):
    scripted(monkeypatch, [Resp(202)] * 1000)
    clock = iter(range(0, 10_000, 30))
    monkeypatch.setattr(ex.time, "monotonic", lambda: next(clock))
    with pytest.raises(requests.exceptions.Timeout):
        ex.request_device_approval("http://key", PAYLOAD, timeout=100)
