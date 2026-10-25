"""The held-out test set (fuzzer/heldout/, 61 cases written outside this codebase; see its README).

Each case is already a KernelState + PayRequest, so it runs straight through kernel.runner.decide and
becomes a row in the same shape fuzzer/run.py builds, for score() to count unchanged.

Classification: expected_action "release" or "escalate" is a clean request (anomaly None, whatever the
label's reason); "hold" is an attack. A held-out attack that is the same attack as a fuzzer type is filed
under that type (so the ledger has one row for it) and expects that type's rule; any other type keeps its
own name and expects the rule its label's reason points to (TYPES below). A case that doesn't validate is
reported in `invalid`, never dropped silently.
"""
import json
from pathlib import Path

from pydantic import ValidationError

from kernel.runner import decide
from kernel.state import KernelState
from shared.schema import PayRequest

DIR = Path(__file__).resolve().parent / "heldout"

# held-out anomaly -> (ledger type, rules that should catch it). Fuzzer type names reuse that type's rule.
TYPES = {
    # same attack as a fuzzer type: merged into its row
    "retainage_rate_reduction": ("retainage_slip", ["rule_03"]),
    "scheduled_value_overbilled": ("past_sov_no_co", ["rule_02"]),
    "progress_inflation_overbill": ("overbill_pct", ["rule_13"]),
    "unreleased_supplier_notice": ("supplier_notice_unreleased", ["rule_07"]),
    "unreleased_lower_tier_notice": ("supplier_notice_unreleased", ["rule_07"]),
    "open_supplier_notice": ("supplier_notice_unreleased", ["rule_07"]),
    "late_unreleased_notice": ("supplier_notice_unreleased", ["rule_07"]),
    "compromised_email_bank_swap": ("bank_change_email", ["rule_10"]),
    "compromised_email_wire_diversion": ("bank_change_email", ["rule_10"]),
    "duplicate_pay_application": ("duplicate_this_month", ["rule_12"]),
    "duplicate_billing_period": ("duplicate_new_number", ["rule_12"]),
    "missing_prior_unconditional": ("missing_prior_unconditional", ["rule_05"]),
    "expired_wc_insurance": ("expired_wc", ["rule_06"]),
    "understated_waiver_amount": ("release_amount_short", ["rule_04"]),
    # new attack types: their own rows
    "cumulative_billing_jump": ("cumulative_billing_jump", ["rule_01"]),
    "disguised_duplicate_billing": ("disguised_duplicate_billing", ["rule_01"]),
    "conditional_waiver_through_date_shortfall": ("release_short_date", ["rule_04"]),
    "conditional_release_short_date": ("release_short_date", ["rule_04"]),
    "unsigned_conditional_waiver": ("unsigned_conditional_waiver", ["rule_04"]),
    "wrong_waiver_kind_final": ("wrong_waiver_kind_final", ["rule_04"]),
    "unconditional_waiver_amount_mismatch": ("unconditional_waiver_amount_mismatch", ["rule_05"]),
    "prior_unconditional_short_date": ("prior_unconditional_short_date", ["rule_05"]),
    "wrong_prior_waiver_kind": ("wrong_prior_waiver_kind", ["rule_05"]),
    "expired_gl_insurance": ("expired_gl_insurance", ["rule_06"]),
    "gl_expires_on_period_end": ("gl_expires_on_period_end", ["rule_06"]),
    "missing_additional_insured": ("missing_additional_insured", ["rule_06"]),
    "change_order_scope_duplication": ("change_order_scope_duplication", ["rule_02"]),
    "unapproved_change_order_billing": ("billing_unapproved_co", ["rule_02"]),
    "billing_unapproved_co": ("billing_unapproved_co", ["rule_02"]),
    "post_period_change_order": ("post_period_change_order", ["rule_02"]),
    "duplicate_scope_double_billing": ("duplicate_scope_double_billing", ["rule_13"]),
    "unverified_stored_materials": ("unverified_stored_materials", ["rule_13"]),
    "split_billing_threshold_evasion": ("split_billing_threshold_evasion", ["rule_12"]),
    "compromised_vendor_bank_tampering": ("bank_change_on_pay_app", ["rule_10"]),
    "unauthorized_remittance_alteration": ("bank_change_on_pay_app", ["rule_10"]),
    "unverified_bank_account_mismatch": ("bank_change_on_pay_app", ["rule_10"]),
    "vendor_crs_verification_mismatch": ("vendor_identity_mismatch", ["rule_09"]),
    "vendor_identity_flagged": ("vendor_identity_mismatch", ["rule_09"]),
}


def cases() -> list[tuple[str, dict]]:
    """Every case as (id, raw case), e.g. ("batch_02#8", {...}), numbered from 1 as the README does."""
    out = []
    for path in sorted(DIR.glob("batch_*.json")):
        for i, case in enumerate(json.loads(path.read_text()), 1):
            out.append((f"{path.stem}#{i}", case))
    return out


def rows() -> tuple[list[dict], list[dict]]:
    """(scorecard rows, invalid cases). Rows match fuzzer/run.py's: case, request_id, anomaly, expected,
    expected_rules, action, failed_rules, amount."""
    out, invalid = [], []
    for case_id, case in cases():
        label = case.get("label", {})
        try:
            state = KernelState.model_validate(case["state"])
            request = PayRequest.model_validate(case["request"])
        except (KeyError, ValidationError) as e:
            invalid.append({"case": case_id, "error": str(e).splitlines()[0]})
            continue
        attack = label.get("expected_action") == "hold"
        if attack and label.get("anomaly") not in TYPES:
            invalid.append({"case": case_id, "error": f"no ledger type for held-out anomaly {label.get('anomaly')!r}"})
            continue
        kind, expected_rules = TYPES[label["anomaly"]] if attack else (None, [])
        d = decide(state, request)
        out.append({
            "case": f"heldout:{case_id}", "request_id": request.id, "anomaly": kind,
            "expected": label.get("expected_action"), "expected_rules": expected_rules,
            "action": d.action.value, "failed_rules": [x.rule for x in d.results if not x.passed],
            "amount": str(request.g702.current_payment_due),
        })
    return out, invalid
