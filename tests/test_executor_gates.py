"""The executor is the last gate before money moves. Each test is an attack that used to pay."""
import time
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

import pipeline
from kernel import executor as ex
from seed import anomalies as a
from seed.generate import generate
from shared.schema import Action, Decision

WORLD = generate()
LIMIT = 25000


def decided(rid, world=WORLD):
    return world, pipeline.decide(world, rid), next(r for r in world["pay_requests"] if r.id == rid)


def signed(priv, req, **overrides):
    payload = {"request_id": req.id, "payee": req.sub_id, "amount": str(req.g702.current_payment_due),
               "job": req.job_id, "nonce": f"n-{req.id}-{time.time_ns()}", "expires_at": int(time.time()) + 90,
               **overrides}
    r, s = decode_dss_signature(priv.sign(ex.canonical_payload_bytes(payload), ec.ECDSA(hashes.SHA256())))
    return ex.SignedApproval(payload=payload, sig_hex=r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex(),
                             nonce=payload["nonce"])


@pytest.fixture
def env(tmp_path, monkeypatch):
    paid = []
    monkeypatch.setattr(ex, "_nessie_pay", lambda req, amt: paid.append((req.id, amt)) or {"request_id": req.id})
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint).hex()
    return priv, pub, paid, tmp_path / "events.db"


def run(env, d, req, approval=None):
    priv, pub, paid, log = env
    return ex.execute(d, req, auto_release_limit_usd=LIMIT, pubkey_hex=pub, approval=approval, log_path=log)


# --- what must still work --------------------------------------------------

def test_clean_small_payment_releases_without_signature(env):
    _, d, req = decided("pr_sub_13_6")
    assert d.action == Action.release
    run(env, d, req)
    assert env[2] == [(req.id, req.g702.current_payment_due)]


def test_owner_approved_escalation_pays(env):
    """Rule 14 failing is what escalation means; with a valid signature the payment goes out."""
    _, d, req = decided("pr_sub_10_6")
    assert d.action == Action.escalate and [r.rule for r in d.results if not r.passed] == ["14_within_spend_authority"]
    run(env, d, req, signed(env[0], req))
    assert env[2] == [(req.id, req.g702.current_payment_due)]


# --- attacks that used to pay ------------------------------------------------

def test_signature_for_one_dollar_cannot_pay_more(env):
    _, d, req = decided("pr_sub_10_6")
    with pytest.raises(ex.ExecutionRefused, match="signed amount"):
        run(env, d, req, signed(env[0], req, amount="1.00"))
    assert env[2] == []


def test_signature_for_another_payee_refused(env):
    _, d, req = decided("pr_sub_10_6")
    with pytest.raises(ex.ExecutionRefused, match="payee"):
        run(env, d, req, signed(env[0], req, payee="sub_99"))


def test_approval_pays_only_once(env):
    _, d, req = decided("pr_sub_10_6")
    approval = signed(env[0], req)
    run(env, d, req, approval)
    with pytest.raises(ex.ExecutionRefused, match="already used"):
        run(env, d, req, approval)  # nonce comes back from the event log, not the caller
    assert len(env[2]) == 1


def test_escalation_under_the_limit_needs_a_signature(env):
    """Every rule passed and it's $9K, but the kernel escalated (e.g. first payment to a new vendor):
    it must not pay without the owner's tap."""
    _, d, req = decided("pr_sub_13_6")
    assert req.g702.current_payment_due < LIMIT and all(r.passed for r in d.results)
    with pytest.raises(ex.ExecutionRefused, match="signed approval"):
        run(env, d.model_copy(update={"action": Action.escalate}), req)
    run(env, d.model_copy(update={"action": Action.escalate}), req, signed(env[0], req))  # with the tap, it pays
    assert len(env[2]) == 1


def test_decision_for_another_request_refused(env):
    _, d, _ = decided("pr_sub_13_6")
    _, _, other = decided("pr_sub_02_6")
    with pytest.raises(ex.ExecutionRefused, match="decision is for"):
        run(env, d, other)


def test_decision_without_rule_results_refused(env):
    _, _, req = decided("pr_sub_13_6")
    with pytest.raises(ex.ExecutionRefused, match="missing rules"):
        run(env, Decision(request_id=req.id, action=Action.release, results=[]), req)


def test_inflated_payment_due_refused(env):
    _, d, req = decided("pr_sub_13_6")
    padded = req.model_copy(update={"g702": req.g702.model_copy(update={"current_payment_due": Decimal("24999.00")})})
    with pytest.raises(ex.ExecutionRefused, match="payment due"):
        run(env, d, padded)


def test_kernel_holds_the_two_sneaky_attacks():
    import random
    for mutate in (a.inflated_payment_due, a.inflated_scheduled_value):
        data, label = mutate(WORLD, "sub_02", random.Random(0))
        d = pipeline.decide(data, label.request_id)
        assert d.action == Action.hold, (label.anomaly, [r.rule for r in d.results if not r.passed])
