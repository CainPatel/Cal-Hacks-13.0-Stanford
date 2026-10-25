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
    sandwich = n.Assessment(summary="Not a payment.", intake=[], holds=[], backstops=[], out_of_scope="it isn't a payment at all")
    out = n.run("make me a sandwich", QueueClient(n.AttackPlan(attack="none", target="", explanation="no match"), sandwich))
    assert out["result"] is None and out["assessment"]["verdict"] == "out_of_scope"
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


class QueueClient(FakeClient):
    """Returns each parsed output in turn: the menu plan, then the assessment."""

    def __init__(self, *outputs):
        super().__init__()
        self._outputs, self.calls = list(outputs), []

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(parsed_output=self._outputs.pop(0), stop_reason="end_turn", _request_id="req_test")


NOT_ON_MENU = n.AttackPlan(attack="none", target="", explanation="nothing on the menu fits")


@pytest.mark.parametrize("assessment,expected", [
    (dict(holds=[{"rule": "10", "why": "new account"}], backstops=[]), "stopped"),
    (dict(holds=[{"rule": "14", "why": "over the limit"}], backstops=[]), "needs_key"),  # 14 escalates, never holds
    (dict(holds=[], backstops=[{"backstop": "limit", "why": "big"}]), "needs_key"),
    (dict(holds=[], backstops=[{"backstop": "recompute", "why": "capped at the contract"}]), "capped"),  # not the key
    (dict(holds=[], backstops=[], jev="Jev would see the odd progress"), "jev"),
    (dict(holds=[], backstops=[], out_of_scope="needs the owner's physical key"), "out_of_scope"),
    (dict(holds=[], backstops=[]), "gap"),
])
def test_the_verdict_is_worked_out_in_code_strongest_first(assessment, expected):
    assert n.verdict(n.Assessment(summary="x", intake=[], **assessment)) == expected


def test_intake_defences_come_before_backstops_but_after_a_rule_hold():
    hidden = [{"defence": "visible_text", "why": "invisible text is ignored"}]
    assert n.verdict(n.Assessment(summary="x", intake=hidden, holds=[], backstops=[{"backstop": "limit", "why": "b"}])) == "blocked"
    assert n.verdict(n.Assessment(summary="x", intake=hidden, holds=[{"rule": "01", "why": "math"}], backstops=[])) == "stopped"
    kept_out = [{"defence": "exact_sender", "why": "not the sub's domain"}]  # never filed, so no rule ever runs
    assert n.verdict(n.Assessment(summary="x", intake=kept_out, holds=[{"rule": "11", "why": "x"}], backstops=[])) == "blocked"


def test_an_idea_off_the_menu_gets_claudes_read_against_the_rules():
    a = n.Assessment(summary="Bills a line twice under two item numbers.", intake=[], holds=[{"rule": "02", "why": "past the contract amount"}], backstops=[])
    client = QueueClient(NOT_ON_MENU, a)
    out = n.run("bill the same drywall twice under two different line numbers", client)
    assert out["result"] is None and out["assessment"]["verdict"] == "stopped"
    sent = client.calls[1]
    assert sent["output_format"] is n.Assessment and "two different line numbers" in sent["messages"][0]["content"]
    assert "Rule 10: the request's payment destination" in sent["messages"][0]["content"]  # the rules' own words


def test_only_real_rules_and_backstops_can_be_named():
    with pytest.raises(Exception):
        n.Assessment(summary="x", intake=[], holds=[{"rule": "16", "why": "made up"}], backstops=[])
    with pytest.raises(Exception):
        n.Assessment(summary="x", intake=[], holds=[], backstops=[{"backstop": "magic", "why": "made up"}])


def test_a_gap_that_says_nothing_gets_through_is_no_gap():
    a = n.Assessment(summary="x", intake=[], holds=[], backstops=[], out_of_scope="no", gap="Nothing extra gets through.")
    assert n.assess("bribe the architect", QueueClient(a)).gap is None
