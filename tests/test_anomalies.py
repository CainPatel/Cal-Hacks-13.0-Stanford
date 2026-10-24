"""Each planted anomaly is really broken in the way its label says, and only its own case is."""
from collections import Counter
from decimal import Decimal

from seed.anomalies import THIS_MONTH, cases, with_demo
from seed.generate import generate
from shared.schema import Action, LedgerKind, ReleaseKind

CLEAN = generate()
CASES = cases(CLEAN)


def req(data, request_id):
    return next(r for r in data["pay_requests"] if r.id == request_id)


def test_spec_counts():
    assert Counter(l.anomaly for _, l in CASES) == {
        "overbill_pct": 4, "retainage_slip": 2, "missing_conditional_release": 3, "expired_wc": 2,
        "past_sov_no_co": 3, "duplicate_new_number": 2, "bank_change_email": 2, "bank_change_homoglyph": 1,
        "supplier_notice_unreleased": 2, "draw_not_cleared": 2, "new_sub_ein_mismatch": 2}
    assert all(l.expected_action == Action.hold and l.rules for _, l in CASES)


def test_clean_data_untouched():
    assert generate() == CLEAN


def test_paperwork_still_adds_up():
    for data, l in CASES:
        r = req(data, l.request_id)
        g = r.g702
        assert g.total_completed_stored == sum(x.total_to_date for x in r.lines)
        assert g.retainage == sum(x.retainage for x in r.lines)
        assert g.current_payment_due == g.total_earned_less_retainage - g.less_previous_certificates
        assert all(x.total_to_date == x.previous_applications + x.this_period + x.stored_materials for x in r.lines)


def test_each_anomaly_is_really_there():
    for data, l in CASES:
        r = req(data, l.request_id)
        vendor = next(v for v in data["vendors"] if v.id == r.sub_id)
        match l.anomaly:
            case "overbill_pct":
                site = next(s for s in data["site_evidence"] if s.job_id == r.job_id and s.id.endswith(f"_{THIS_MONTH}"))
                assert max(x.pct_complete - site.estimates[x.item_no] for x in r.lines) > 10
            case "retainage_slip":
                assert r.g702.retainage < r.g702.total_completed_stored * Decimal("0.10")
            case "missing_conditional_release":
                assert not any(x.sub_id == r.sub_id and x.kind == ReleaseKind.conditional_progress
                               and x.through_date == r.period_end for x in data["releases"])
            case "expired_wc":
                assert next(c for c in data["cois"] if c.sub_id == r.sub_id).wc_expiry < r.period_end
            case "past_sov_no_co":
                assert any(x.total_to_date > x.scheduled_value for x in r.lines)
                assert not any(c.approved and c.sub_id == r.sub_id for c in data["change_orders"])
            case "duplicate_new_number":
                twin = req(data, l.request_id.removesuffix("_resub"))
                assert [x.total_to_date for x in r.lines] == [x.total_to_date for x in twin.lines]
                assert r.number != twin.number
            case "bank_change_email" | "bank_change_homoglyph":
                assert r.bank_account != vendor.bank_account
                email = next(e for e in data["emails"] if e.claims.new_account == r.bank_account)
                assert (email.from_domain != vendor.email_domain) == (l.anomaly == "bank_change_homoglyph")
            case "supplier_notice_unreleased":
                assert any(n.sub_id == r.sub_id and not n.released for n in data["lower_tier_notices"])
            case "draw_not_cleared":
                assert not any(e.kind == LedgerKind.owner_draw and e.job_id == r.job_id
                               and e.period_end == r.period_end for e in data["ledger"])
            case "new_sub_ein_mismatch":
                assert vendor.first_paid is None and vendor.crs_status == "mismatch"
            case _:
                raise AssertionError(l.anomaly)


def test_demo_db_labels_every_request():
    data, labels = with_demo(CLEAN)
    assert {l.request_id for l in labels} == {r.id for r in data["pay_requests"]}
    assert sorted(l.request_id for l in labels if l.anomaly) == ["pr_sub_01_6", "pr_sub_03_6", "pr_sub_04_6"]
    drywall = req(data, "pr_sub_03_6")
    assert next(x for x in drywall.lines if x.item_no == "09-20").pct_complete == 70


def test_attacks_beyond_the_spec_list():
    from seed.anomalies import duplicate_this_month, missing_prior_unconditional
    import random
    data, lab = duplicate_this_month(CLEAN, "sub_02", random.Random(0))
    dup, orig = req(data, lab.request_id), req(data, "pr_sub_02_6")
    assert dup.lines == orig.lines and dup.number != orig.number and lab.rules == ["rule_12"]
    data, lab = missing_prior_unconditional(CLEAN, "sub_02", random.Random(0))
    assert not any(r.id == "rel_u_pr_sub_02_5" for r in data["releases"]) and lab.rules == ["rule_05"]


def test_sneaky_attacks_keep_the_paperwork_consistent():
    import random
    from seed.anomalies import inflated_payment_due, inflated_scheduled_value
    data, lab = inflated_scheduled_value(CLEAN, "sub_02", random.Random(0), extra_points=25)
    r = req(data, lab.request_id)
    contract = {l.item_no: l.scheduled_value for c in data["subcontracts"] if c.sub_id == "sub_02" for l in c.sov}
    site = next(s for s in data["site_evidence"] if s.id == f"site_{r.job_id}_6")
    # the printed % matches the video, but against the contract's SOV it's 25 points higher
    assert all(l.pct_complete == site.estimates[l.item_no] or l.scheduled_value == contract[l.item_no] for l in r.lines)
    assert max(l.total_to_date / contract[l.item_no] * 100 - site.estimates[l.item_no] for l in r.lines) >= 24
    assert r.g702.current_payment_due == r.g702.total_earned_less_retainage - r.g702.less_previous_certificates
    data, lab = inflated_payment_due(CLEAN, "sub_02", random.Random(0), factor=1.5)
    r, clean = req(data, lab.request_id), req(CLEAN, "pr_sub_02_6")
    assert r.lines == clean.lines and r.g702.current_payment_due > clean.g702.current_payment_due
