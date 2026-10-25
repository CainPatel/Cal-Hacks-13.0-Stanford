"""Plain-English attacks: Claude's plan is limited to the menu, and runs through the real attack path."""
from types import SimpleNamespace

import pytest

from fuzzer import nl_attack as n


class FakeClient:
    """Stands in for anthropic.Anthropic: returns a fixed plan and records the request."""

    def __init__(self, plan=None, stop_reason="end_turn"):
        self.sent = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))
        self._plan, self._stop = plan, stop_reason

    def parse(self, **kw):
        self.sent = kw
        return SimpleNamespace(parsed_output=self._plan, stop_reason=self._stop, _request_id="req_test")


def test_retainage_waiver_story():
    plan = n.AttackPlan(attack="retainage_slip", target="sub_04", rate="0",
                        explanation="Brightline bills with no retainage withheld.", homoglyph=True)
    client = FakeClient(plan)
    out = n.run("the electrician says the owner told them retainage is waived this month", client)
    assert out["result"]["label"].anomaly == "retainage_slip"
    assert out["result"]["label"].request_id == "pr_sub_04_6"
    assert client.sent["model"] == "claude-opus-5-5" and client.sent["output_format"] is n.AttackPlan
    assert "retainage is waived" in client.sent["messages"][0]["content"]  # judge text goes in as data


def test_settings_only_for_the_chosen_mutator():
    plan = n.AttackPlan(attack="expired_wc", target="sub_06", expiry="2026-09-01", homoglyph=True, explanation="x")
    assert n.settings_for(plan) == {"expiry": __import__("datetime").date(2026, 9, 1)}


def test_none_and_refusal():
    assert n.run("make me a sandwich", FakeClient(n.AttackPlan(attack="none", target="", explanation="no match")))["result"] is None
    with pytest.raises(n.AttackError):
        n.plan("anything", FakeClient(None, stop_reason="refusal"))


def test_roster_names_every_sub():
    r = n.roster()
    assert len(r) == 15 and {"id", "name", "trade", "job", "job_name"} <= set(r[0])


@pytest.mark.parametrize("text,expected", [("0%", "0"), ("0", "0"), ("5%", "0.05"), ("5", "0.05"), ("0.05", "0.05"),
                                           (" 10 % ", "0.1")])
def test_retainage_rate_is_normalized(text, expected):
    from fuzzer.nl_attack import _fraction
    assert _fraction(text) == expected


@pytest.mark.parametrize("text", ["waived", "", "-5%", "150%"])
def test_bad_retainage_rate_is_a_value_error(text):
    from fuzzer.nl_attack import _fraction
    with pytest.raises(ValueError):
        _fraction(text)


def test_waived_retainage_story_runs_end_to_end():
    """The exact plan Claude produced live ("0%") used to crash the mutator with InvalidOperation."""
    from fuzzer import nl_attack, run as fuzzer
    plan = nl_attack.AttackPlan(attack="retainage_slip", target="sub_04", explanation="waived", rate="0%")
    out = fuzzer.attack(plan.attack, plan.target, **nl_attack.settings_for(plan))
    assert out["decision"].action.value == "hold"


@pytest.mark.parametrize("attack,target,expected", [
    ("bank_change_email", "", "sub_01"),           # no sub named: the first one it applies to
    ("bank_change_email", "Brightline", "sub_01"),  # not an id: same
    ("duplicate_new_number", "job_2", "sub_06"),   # a job for a sub attack: that job's first sub
    ("expired_wc", "sub_06", "sub_06"),
    ("new_sub_ein_mismatch", "", "job_1"),         # a new sub joins a job
    ("new_sub_ein_mismatch", "sub_06", "job_2"),   # a sub given: its job
])
def test_a_missing_or_mismatched_target_still_runs(attack, target, expected):
    plan = n.AttackPlan(attack=attack, target=target, explanation="x")
    out = n.run("any description", FakeClient(plan))
    assert out["plan"]["target"] == expected and out["result"]["decision"] is not None


def test_a_date_that_isnt_one_falls_back_to_the_attacks_own():
    plan = n.AttackPlan(attack="expired_wc", target="sub_01", expiry="last week", explanation="x")
    assert n.settings_for(plan) == {}
    assert n.run("the concrete sub's workers comp expired last week", FakeClient(plan))["result"]["label"].anomaly == "expired_wc"
