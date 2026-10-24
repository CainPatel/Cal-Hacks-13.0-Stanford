"""Every rule ships with one passing and one failing fixture (CLAUDE.md hard rule #7)."""
import json
from pathlib import Path

import pytest

from kernel.rules import ALL_RULES
from kernel.state import KernelState
from shared.schema import PayRequest

FIXTURES = Path(__file__).parent / "fixtures"
RULE_NUMBERS = ["01", "02", "03"]


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


def test_runner_releases_on_all_pass():
    from kernel.runner import decide
    from shared.schema import Action

    state, request = load("rule_01_pass.json")
    decision = decide(state, request)
    assert decision.action == Action.release
    assert all(r.passed for r in decision.results)


def test_runner_holds_on_any_fail():
    from kernel.runner import decide
    from shared.schema import Action

    state, request = load("rule_02_fail.json")
    decision = decide(state, request)
    assert decision.action == Action.hold
    assert decision.next_action
