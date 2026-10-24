"""The fuzzer's cases are real mutations and its scorecard math is right."""
import random

import pytest

from fuzzer import run as f
from shared.schema import Action, Decision


def test_cases_are_mutated_and_cover_every_type():
    rng = random.Random(1)
    worlds = {id(w): w for w in [f.generate(rng_seed=s) for s in range(f.WORLDS)]}
    seen = set()
    for data, label in f.make_cases(400, rng):
        assert any(r.id == label.request_id for r in data["pay_requests"])
        if label.anomaly is None:
            assert label.expected_action == Action.release
        else:
            assert label.expected_action == Action.hold and label.rules
            assert all(data != w for w in worlds.values())  # something actually changed
            seen.update(label.anomaly.split("+"))
    assert len(seen) == 13  # every anomaly type, incl. both bank-change variants


def test_scorecard_with_hold_everything_kernel(monkeypatch):
    monkeypatch.setattr(f, "decide", lambda state, rid, **kw: Decision(request_id=rid, action=Action.hold, results=[]))
    card = f.run(200, rng_seed=3)
    assert card["cases"] == 200 and card["mutated"] + card["clean"] == 200
    assert card["wrongful_releases"] == 0 and card["catch_rate"] == 1.0 and card["false_hold_rate"] == 1.0
    assert len(card["misses"]) == min(50, card["clean"])  # every clean hold is a miss


def test_stub_kernel_is_obvious():
    if "STUB" not in f.KERNEL:
        pytest.skip("real kernel is installed")
    card = f.run(100)
    assert card["wrongful_releases"] == card["mutated"] and "STUB" in card["kernel"]


def test_attack_console():
    out = f.attack("bank_change_email", "sub_04", homoglyph=True)
    assert out["label"].anomaly == "bank_change_homoglyph" and out["label"].rules == ["rule_10", "rule_11"]
    assert out["decision"].request_id == out["label"].request_id
    assert set(f.ATTACKS) >= {"overbill_pct", "new_sub_ein_mismatch", "draw_not_cleared"}


def test_rules_only_headline_ignores_the_button(monkeypatch):
    monkeypatch.setattr(f, "decide", lambda state, rid, **kw: Decision(request_id=rid, action=Action.escalate, results=[]))
    card = f.run(100)
    assert card["wrongful_releases"] == 0  # the button stopped them...
    assert card["wrongful_if_every_button_approved"] == card["mutated"]  # ...but no rule did
    assert card["rule_catch_rate"] == 0.0
    assert card["button_presses_per_job_month"] == 5.0  # every one of a job's 5 subs needed a tap


def test_limit_sweep_trades_taps_for_risk(monkeypatch):
    def by_limit(state, rid, contract=None):
        req = next(r for r in state["pay_requests"] if r.id == rid)
        over = req.g702.current_payment_due > (contract or {"auto_release_limit_usd": 25000})["auto_release_limit_usd"]
        return Decision(request_id=rid, action=Action.escalate if over else Action.release, results=[])

    monkeypatch.setattr(f, "decide", by_limit)
    sweep = f.run(200)["limit_sweep"]
    assert [s["limit"] for s in sweep] == f.LIMITS
    taps = [s["button_presses_per_job_month"] for s in sweep]
    leaked = [s["attacks_auto_paid"] for s in sweep]
    assert taps == sorted(taps, reverse=True) and leaked == sorted(leaked)  # higher limit: fewer taps, more risk
