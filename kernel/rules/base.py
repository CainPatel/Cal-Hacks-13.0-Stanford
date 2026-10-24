"""Shared helpers for rule functions. A rule is (KernelState, PayRequest) -> RuleResult."""
from typing import Callable

from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

Rule = Callable[[KernelState, PayRequest], RuleResult]


def ok(rule: str, evidence: dict, message: str) -> RuleResult:
    return RuleResult(rule=rule, passed=True, evidence=evidence, message=message)


def fail(rule: str, evidence: dict, message: str) -> RuleResult:
    return RuleResult(rule=rule, passed=False, evidence=evidence, message=message)
