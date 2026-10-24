"""Rule 11: every email on file for this sub must come from exactly the vendor's domain.
The $1.5M wire started with a capital I standing in for a lowercase l -- this rule flags
not just a mismatch but which characters are classic lookalikes, for the evidence trace.
"""
from kernel.rules.base import fail, ok
from kernel.state import KernelState
from shared.schema import PayRequest, RuleResult

RULE = "11_sender_domain_authentic"

# Characters commonly swapped in lookalike domains (ASCII mix-ups and Cyrillic homoglyphs).
_CONFUSABLE_GROUPS = [
    {"l", "I", "1", "|"},
    {"O", "0", "o"},
    {"S", "5"},
    {"B", "8"},
    {"a", "а"},  # Cyrillic а (U+0430)
    {"e", "е"},  # Cyrillic е (U+0435)
    {"p", "р"},  # Cyrillic р (U+0440)
    {"c", "с"},  # Cyrillic с (U+0441)
    {"m", "rn"},
    {"w", "vv"},
]


def _is_confusable(a: str, b: str) -> bool:
    return any({a, b} <= group for group in _CONFUSABLE_GROUPS)


def _diff(claimed: str, vendor: str) -> list[dict]:
    diffs = [
        {"position": i, "claimed": ca, "vendor": cb, "confusable": _is_confusable(ca, cb)}
        for i, (ca, cb) in enumerate(zip(claimed, vendor)) if ca != cb
    ]
    if len(claimed) != len(vendor):
        diffs.append({"position": "length", "claimed": len(claimed), "vendor": len(vendor), "confusable": False})
    return diffs


def check(state: KernelState, request: PayRequest) -> RuleResult:
    vendor_domain = state.vendor.email_domain
    mismatches = [
        {"email_id": e.id, "claimed_domain": e.from_domain, "vendor_domain": vendor_domain,
         "differences": _diff(e.from_domain, vendor_domain)}
        for e in state.emails if e.from_domain != vendor_domain
    ]
    if mismatches:
        return fail(RULE, {"mismatches": mismatches},
                     f"{len(mismatches)} email(s) claim to be from {vendor_domain} but the sending domain doesn't match exactly.")
    return ok(RULE, {"emails_checked": len(state.emails), "vendor_domain": vendor_domain},
               "Every email on file matches the vendor's domain exactly.")
