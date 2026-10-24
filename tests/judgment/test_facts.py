import json
from decimal import Decimal as D

import pytest

from judgment.facts import MAX_STATE_BYTES, compute_facts, domain_resembles, is_lookalike
from seed.anomalies import THIS_MONTH
from tests.judgment.conftest import state_for


def facts_for(data, request_id):
    return compute_facts(*state_for(data, request_id))


def case(anomaly_cases, anomaly):
    return next((d, l) for d, l in anomaly_cases if l.anomaly == anomaly)


@pytest.mark.parametrize(
    "sender,vendor,expected",
    [
        ("ridgeIine.com", "ridgeline.com", True),  # capital I for l
        ("rnason.com", "mason.com", True),  # rn for m
        ("c0astal.com", "coastal.com", True),  # zero for o
        ("Ridgeline.com", "ridgeline.com", False),  # same domain, different case
        ("ridgeline.com", "ridgeline.com", False),
        ("evil.com", "ridgeline.com", False),  # different, but not a lookalike
    ],
)
def test_is_lookalike(sender, vendor, expected):
    assert is_lookalike(sender, vendor) is expected


def test_clean_request_facts(seed):
    clean = next(r for r in seed["pay_requests"] if r.number == THIS_MONTH)
    f = facts_for(seed, clean.id)
    assert f["release"]["signed_release_of_required_kind_through_this_period"]
    assert f["request"]["sub_summary_differs_from_computed"] is False
    assert f["bank"]["account_changed"] is False
    assert not any(e["every_line_same_total_to_date"] for e in f["duplicates"]["earlier_requests"])
    c = f["contract"]
    assert c["lines_withholding_less_than_contract_retainage"] == [] and c["owner_draw_received_for_period"]
    assert f["vendor"]["first_payment_to_vendor"] is False


def test_overbill_shows_a_gap_over_tolerance(anomaly_cases):
    data, label = case(anomaly_cases, "overbill_pct")
    assert any(l["gap_exceeds_tolerance"] for l in facts_for(data, label.request_id)["progress"]["lines"])


def test_homoglyph_email_is_flagged(anomaly_cases):
    data, label = case(anomaly_cases, "bank_change_homoglyph")
    bank = facts_for(data, label.request_id)["bank"]
    assert bank["account_changed"]
    assert any(e["domain_imitates_vendor"] and e["names_this_requests_account"] for e in bank["emails"])


def test_bank_change_email_is_flagged(anomaly_cases):
    data, label = case(anomaly_cases, "bank_change_email")
    bank = facts_for(data, label.request_id)["bank"]
    assert bank["account_changed"] and any(e["asks_for_bank_change"] for e in bank["emails"])


def test_missing_release_is_visible(anomaly_cases):
    data, label = case(anomaly_cases, "missing_conditional_release")
    assert not facts_for(data, label.request_id)["release"]["signed_release_of_required_kind_through_this_period"]


def test_resubmission_matches_an_earlier_paid_request(anomaly_cases):
    data, label = case(anomaly_cases, "duplicate_new_number")
    dupes = facts_for(data, label.request_id)["duplicates"]["earlier_requests"]
    assert any(d["every_line_same_total_to_date"] and d["already_paid"] and not d["same_number"] for d in dupes)


def test_new_vendor_flagged(anomaly_cases):
    data, label = case(anomaly_cases, "new_sub_ein_mismatch")
    assert facts_for(data, label.request_id)["vendor"]["first_payment_to_vendor"] is True


def test_every_case_fits_jev_size_limit_and_is_json(seed, anomaly_cases):
    sets = [(seed, r.id) for r in seed["pay_requests"] if r.number == THIS_MONTH]
    sets += [(d, l.request_id) for d, l in anomaly_cases]
    for data, request_id in sets:
        payload = json.dumps(facts_for(data, request_id))
        assert len(payload.encode()) <= MAX_STATE_BYTES


def test_payment_due_figures_retainage_per_line_like_the_kernel():
    """Rounding retainage once on the total would put the computed due a cent off the sub's G702."""
    from types import SimpleNamespace
    from judgment.facts import computed_payment_due
    from shared.schema import LineItem
    line = lambda n: LineItem(item_no=n, description="x", scheduled_value=D("1"), previous_applications=D("0"),  # noqa: E731
                              this_period=D("0.05"), stored_materials=D("0"), total_to_date=D("0.05"),
                              pct_complete=D("5"), balance_to_finish=D("0.95"), retainage=D("0.01"))
    state = SimpleNamespace(subcontract=SimpleNamespace(retainage_rate=D("0.10")), ledger=[])
    request = SimpleNamespace(lines=[line("1"), line("2")], sub_id="s", job_id="j", id="r")
    assert computed_payment_due(state, request) == D("0.08")  # 0.10 billed, 0.01 + 0.01 held


def test_a_long_schedule_of_values_lists_only_lines_with_something_to_judge(seed):
    """A GC-sized sheet (150+ lines, most billing nothing yet) still fits Jev's limit, and says what it left out."""
    from shared.schema import LineItem, SOVLine
    request_id = next(r.id for r in seed["pay_requests"] if r.number == THIS_MONTH)
    state, req = state_for(seed, request_id)
    idle = [LineItem(item_no=f"9{i:03d}", description=f"Division {i} work not yet started", scheduled_value=D("10000"),
                     previous_applications=D("0"), this_period=D("0"), stored_materials=D("0"), total_to_date=D("0"),
                     pct_complete=D("0"), balance_to_finish=D("10000"), retainage=D("0")) for i in range(150)]
    req = req.model_copy(update={"lines": req.lines + idle})
    state.subcontract = state.subcontract.model_copy(update={"sov": state.subcontract.sov + [
        SOVLine(item_no=l.item_no, description=l.description, scheduled_value=l.scheduled_value) for l in idle]})
    facts = compute_facts(state, req)
    progress = facts["progress"]
    assert len(json.dumps(facts).encode()) <= MAX_STATE_BYTES
    assert progress["lines_not_listed"]["count"] == 150
    assert [l["item_no"] for l in progress["lines"]] == [l.item_no for l in req.lines[:-150]]


def test_full_bank_account_numbers_never_leave(anomaly_cases):
    data, label = case(anomaly_cases, "bank_change_homoglyph")
    state, req = state_for(data, label.request_id)
    payload = json.dumps(compute_facts(state, req))
    assert req.bank_account not in payload and state.vendor.bank_account not in payload


@pytest.mark.parametrize(
    "text,expected",
    [
        ("send to 447712782:5071050724 today", "send to ...0724 today"),
        ("routing 021000021 acct 1234567890", "routing ...0021 acct ...7890"),
        ("invoice 1042 for $5,000", "invoice 1042 for $5,000"),  # short numbers and money stay
        ("due 2026-10-31 please", "due 2026-10-31 please"),
    ],
)
def test_redact(text, expected):
    from judgment.facts import redact

    assert redact(text) == expected


def test_problems_found_matches_the_kernel_on_every_case(seed, anomaly_cases):
    """No problem listed on a request the rules let through; at least one on every request the rules hold."""
    import pipeline
    from tests.judgment.conftest import CONTRACT
    sets = [(seed, r.id) for r in seed["pay_requests"] if r.number == THIS_MONTH]
    sets += [(d, l.request_id) for d, l in anomaly_cases]
    for data, rid in sets:
        problems = compute_facts(*state_for(data, rid))["problems_found"]
        action = pipeline.decide(data, rid, contract=CONTRACT).action.value
        assert bool(problems) == (action == "hold"), (rid, action, problems)


def test_over_billing_problem_means_a_line_above_the_video(seed, anomaly_cases):
    """Rule 13 holds over-billing only; billing below the video (e.g. an old request resubmitted) is not it."""
    sets = [(seed, r.id) for r in seed["pay_requests"] if r.number == THIS_MONTH]
    sets += [(d, l.request_id) for d, l in anomaly_cases]
    for data, rid in sets:
        f = facts_for(data, rid)
        flagged = "claimed progress exceeds what the site video shows" in f["problems_found"]
        assert flagged == bool(f["progress"]["lines_billed_above_video_beyond_tolerance"]), rid
