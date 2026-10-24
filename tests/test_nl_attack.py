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
