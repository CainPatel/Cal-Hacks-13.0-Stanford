"""Registry of all kernel rules, in order. Rules 04-15 land here as they're written."""
from kernel.rules import (
    rule_01_cumulative_continuity,
    rule_02_scheduled_value_ceiling,
    rule_03_retainage_math,
)
from kernel.rules.base import Rule

ALL_RULES: list[Rule] = [
    rule_01_cumulative_continuity.check,
    rule_02_scheduled_value_ceiling.check,
    rule_03_retainage_math.check,
]
