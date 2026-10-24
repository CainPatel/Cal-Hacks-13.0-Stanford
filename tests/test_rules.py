"""Every rule ships with one passing and one failing fixture (CLAUDE.md hard rule #7)."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from kernel.rules import ALL_RULES
from kernel.state import KernelState
from shared.schema import PayRequest

FIXTURES = Path(__file__).parent / "fixtures"
RULE_NUMBERS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15"]
ESCALATION_RULES = {"14", "15"}


def load(name: str) -> tuple[KernelState, PayRequest]:
    raw = json.loads((FIXTURES / name).read_text())
    return KernelState.model_validate(raw["state"]), PayRequest.model_validate(raw["request"])


@pytest.mark.parametrize("n", RULE_NUMBERS)
def test_rule_passes_its_pass_fixture(n):
    state, request = load(f"rule_{n}_pass.json")
    rule = next(r for r in ALL_RULES if r.__module__.split(".")[-1].startswith(f"rule_{n}_"))
    result = rule(state, request)
    assert result.passed, result.message


@pytest.mark.parametrize("n", RULE_NUMBERS)
def test_rule_fails_its_fail_fixture(n):
    state, request = load(f"rule_{n}_fail.json")
    rule = next(r for r in ALL_RULES if r.__module__.split(".")[-1].startswith(f"rule_{n}_"))
    result = rule(state, request)
    assert not result.passed, result.message


@pytest.mark.parametrize("n", RULE_NUMBERS)
def test_fail_fixture_is_isolated(n):
    """A fail fixture should break exactly its own rule and nothing else -- if the shared
    base_state() in the fixture generator drifts (e.g. forgets a COI or a release), one
    rule's fail fixture silently starts failing other rules too, which hides bugs and
    makes next_action_for() point at the wrong rule. Catches exactly the kind of mistake
    that slipped in while wiring rule 06's COI into base_state().
    """
    state, request = load(f"rule_{n}_fail.json")
    results = {}
    for rule in ALL_RULES:
        result = rule(state, request)
        results[result.rule.split("_", 1)[0]] = result
    assert not results[n].passed, f"rule {n} was expected to fail its own fail fixture"
    other_failures = [num for num, r in results.items() if num != n and not r.passed]
    assert not other_failures, f"fail fixture for rule {n} unexpectedly also fails rule(s) {other_failures}"


@pytest.mark.parametrize("n", RULE_NUMBERS)
def test_pass_fixture_passes_every_rule(n):
    """A pass fixture is supposed to be a fully clean request: every rule should pass,
    not just the one it's nominally testing.
    """
    state, request = load(f"rule_{n}_pass.json")
    for rule in ALL_RULES:
        result = rule(state, request)
        assert result.passed, f"rule_{n}_pass.json unexpectedly fails {result.rule}: {result.message}"


def test_rule_02_ceiling_comes_from_contract_not_self_reported_scheduled_value():
    """A sub could inflate LineItem.scheduled_value on their own G703 to raise their own
    billing ceiling. The ceiling must come from the subcontract's SOV, not the request.
    """
    state, request = load("rule_02_pass.json")
    from kernel.rules.rule_02_scheduled_value_ceiling import check

    # contract SOV for item 09-10 is 100,000.00; inflate the sub's self-reported value
    # and the claimed total-to-date to match, well past what the contract actually allows
    inflated_line = request.lines[0].model_copy(update={
        "scheduled_value": Decimal("500000.00"),
        "total_to_date": Decimal("250000.00"),
    })
    forged_request = request.model_copy(update={"lines": [inflated_line]})

    result = check(state, forged_request)
    assert not result.passed, "rule 02 must not trust the sub's self-reported scheduled_value"


def test_runner_releases_on_all_pass():
    from kernel.runner import decide
    from shared.schema import Action

    state, request = load("rule_01_pass.json")
    decision = decide(state, request)
    assert decision.action == Action.release
    assert all(r.passed for r in decision.results)


def test_runner_escalates_over_limit_with_yaml_loaded_contract():
    """auto_release_limit_usd comes from yaml as an int/str, not a Decimal -- rule 14
    must not blow up comparing Decimal to that raw type."""
    from kernel.runner import decide
    from shared.schema import Action

    state, request = load("rule_01_pass.json")
    state = state.model_copy(update={"contract": {"auto_release_limit_usd": 5000}})
    decision = decide(state, request)
    assert decision.action == Action.escalate


def test_runner_holds_on_any_fail():
    from kernel.runner import decide
    from shared.schema import Action

    state, request = load("rule_02_fail.json")
    decision = decide(state, request)
    assert decision.action == Action.hold
    assert decision.next_action


@pytest.mark.parametrize("n", sorted(ESCALATION_RULES))
def test_runner_escalates_not_holds_on_escalation_rule_failure(n):
    """Rules 14 and 15 failing means "needs a physical sign-off," not "something's
    wrong" -- the decision must be escalate, with no next_action (that's only for holds).
    """
    from kernel.runner import decide
    from shared.schema import Action

    state, request = load(f"rule_{n}_fail.json")
    decision = decide(state, request)
    assert decision.action == Action.escalate
    assert decision.next_action is None


def test_runner_holds_over_escalates_when_both_fail():
    """A hard rule failure must win over an escalation-rule failure -- an over-limit
    request that's also missing its release should be held, not escalated.
    """
    from kernel.runner import decide
    from shared.schema import Action

    state, request = load("rule_04_fail.json")
    state = state.model_copy(update={"contract": {"auto_release_limit_usd": 5000}})
    decision = decide(state, request)
    assert decision.action == Action.hold
    assert decision.next_action
