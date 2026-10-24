import json

import pytest

from judgment.facts import MAX_STATE_BYTES, compute_facts, is_lookalike
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
    assert f["release"]["release_on_file"] and f["release"]["signed"]
    assert f["release"]["kind_matches_required"] and f["release"]["through_date_vs_period_end_days"] == 0
    assert f["bank"]["account_changed"] is False
    assert not any(e["all_lines_identical"] for e in f["duplicates"]["earlier_requests"])
    assert f["vendor"]["first_payment_to_vendor"] is False


def test_overbill_shows_a_gap_over_tolerance(anomaly_cases):
    data, label = case(anomaly_cases, "overbill_pct")
    assert any(l["gap_exceeds_tolerance"] for l in facts_for(data, label.request_id)["progress"]["lines"])


def test_homoglyph_email_is_flagged(anomaly_cases):
    data, label = case(anomaly_cases, "bank_change_homoglyph")
    bank = facts_for(data, label.request_id)["bank"]
    assert bank["account_changed"]
    assert any(e["domain_is_lookalike_of_vendor"] and e["new_account_is_this_requests_account"] for e in bank["emails"])


def test_bank_change_email_is_flagged(anomaly_cases):
    data, label = case(anomaly_cases, "bank_change_email")
    bank = facts_for(data, label.request_id)["bank"]
    assert bank["account_changed"] and any(e["asks_for_bank_change"] for e in bank["emails"])


def test_missing_release_is_visible(anomaly_cases):
    data, label = case(anomaly_cases, "missing_conditional_release")
    r = facts_for(data, label.request_id)["release"]
    assert r["releases_through_this_period"] == 0 or not r.get("signed", False)


def test_resubmission_matches_an_earlier_paid_request(anomaly_cases):
    data, label = case(anomaly_cases, "duplicate_new_number")
    dupes = facts_for(data, label.request_id)["duplicates"]["earlier_requests"]
    assert any(d["all_lines_identical"] and d["already_paid"] and not d["same_number"] for d in dupes)


def test_new_vendor_flagged(anomaly_cases):
    data, label = case(anomaly_cases, "new_sub_ein_mismatch")
    assert facts_for(data, label.request_id)["vendor"]["first_payment_to_vendor"] is True


def test_every_case_fits_jev_size_limit_and_is_json(seed, anomaly_cases):
    sets = [(seed, r.id) for r in seed["pay_requests"] if r.number == THIS_MONTH]
    sets += [(d, l.request_id) for d, l in anomaly_cases]
    for data, request_id in sets:
        payload = json.dumps(facts_for(data, request_id))
        assert len(payload.encode()) <= MAX_STATE_BYTES


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
