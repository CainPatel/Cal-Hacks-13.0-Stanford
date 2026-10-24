"""Registry of all kernel rules, in order. Rules 04-15 land here as they're written."""
from kernel.rules import (
    rule_01_cumulative_continuity,
    rule_02_scheduled_value_ceiling,
    rule_03_retainage_math,
    rule_04_conditional_release_present,
    rule_05_prior_unconditional_received,
    rule_06_insurance_current,
    rule_07_lower_tier_released,
    rule_08_owner_draw_funded,
    rule_09_vendor_verified,
    rule_10_bank_details_unchanged,
    rule_11_sender_domain_authentic,
    rule_12_not_a_duplicate,
    rule_13_progress_plausible,
    rule_14_within_spend_authority,
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
    rule_08_owner_draw_funded.check,
    rule_09_vendor_verified.check,
    rule_10_bank_details_unchanged.check,
    rule_11_sender_domain_authentic.check,
    rule_12_not_a_duplicate.check,
    rule_13_progress_plausible.check,
    rule_14_within_spend_authority.check,
    rule_15_confidence_floor.check,
]
