"""Registry of all kernel rules, in order. Rules 04-15 land here as they're written."""
from kernel.rules import (
    rule_01_cumulative_continuity,
    rule_02_scheduled_value_ceiling,
    rule_03_retainage_math,
    rule_04_conditional_release_present,
    rule_05_prior_unconditional_received,
    rule_06_insurance_current,
    rule_07_lower_tier_released,
    rule_15_confidence_floor,
)
from kernel.rules.base import Rule

ALL_RULES: list[Rule] = [
    rule_01_cumulative_continuity.check,
    rule_02_scheduled_value_ceiling.check,
    rule_03_retainage_math.check,
    rule_04_conditional_release_present.check,
    rule_05_prior_unconditional_received.check,
    rule_06_insurance_current.check,
    rule_07_lower_tier_released.check,
    rule_15_confidence_floor.check,
]
