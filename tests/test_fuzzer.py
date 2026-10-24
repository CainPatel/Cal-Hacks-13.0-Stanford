"""The fuzzer's cases are real mutations and its scorecard math is right."""
import random

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
    assert len(seen) == 11  # every anomaly type, incl. both bank-change variants


def test_scorecard_with_hold_everything_kernel(monkeypatch):
    monkeypatch.setattr(f, "decide", lambda state, rid: Decision(request_id=rid, action=Action.hold, results=[]))
    card = f.run(200, rng_seed=3)
    assert card["cases"] == 200 and card["mutated"] + card["clean"] == 200
    assert card["wrongful_releases"] == 0 and card["catch_rate"] == 1.0 and card["false_hold_rate"] == 1.0
    assert len(card["misses"]) == min(50, card["clean"])  # every clean hold is a miss


def test_stub_kernel_is_obvious():
    card = f.run(100)
    assert card["wrongful_releases"] == card["mutated"] and "STUB" in card["kernel"]
