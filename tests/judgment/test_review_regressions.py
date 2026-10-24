"""Regressions for an adversarial review of judgment/. Each case was reproduced against the earlier code."""

import http.client
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from judgment import fallback, jev
from judgment.facts import compute_facts, domain_resembles, redact
from judgment.judge import InvalidAnswers, judge, validate
from judgment.questions import ACTION, BANK, QUESTIONS, RELEASE
from seed.anomalies import THIS_MONTH
from shared.schema import Email, EmailClaims
from tests.judgment.conftest import state_for
from tests.judgment.test_judge import FakeClaude, FakeResponse, claude_answers, jev_answers

D = Decimal


def case(anomaly_cases, anomaly):
    return next((d, l) for d, l in anomaly_cases if l.anomaly == anomaly)


def swap_request(data, request_id, **changes):
    data = {k: list(v) for k, v in data.items()}
    i = next(i for i, r in enumerate(data["pay_requests"]) if r.id == request_id)
    data["pay_requests"][i] = data["pay_requests"][i].model_copy(update=changes)
    return data


# --- facts must come from the dollars and the ledger, not the sub's own figures


def test_overbill_survives_a_lowered_percent_column(anomaly_cases):
    data, label = case(anomaly_cases, "overbill_pct")
    req = next(r for r in data["pay_requests"] if r.id == label.request_id)
    hidden = [l.model_copy(update={"pct_complete": D("0")}) for l in req.lines]  # percent column lies
    f = compute_facts(*state_for(swap_request(data, req.id, lines=hidden), req.id))
    assert any(l["gap_exceeds_tolerance"] and l["percent_column_disagrees_with_billed"] for l in f["progress"]["lines"])


def test_lowered_g702_due_does_not_hide_the_limit(seed):
    req = max((r for r in seed["pay_requests"] if r.number == THIS_MONTH), key=lambda r: r.g702.current_payment_due)
    assert req.g702.current_payment_due > 25000
    g702 = req.g702.model_copy(update={"current_payment_due": D("24000.00")})
    f = compute_facts(*state_for(swap_request(seed, req.id, g702=g702), req.id))["request"]
    assert f["over_auto_release_limit"] and f["sub_summary_differs_from_computed"]
    assert D(f["payment_due_computed"]) == req.g702.current_payment_due


def test_contract_facts_expose_the_other_anomalies(anomaly_cases):
    checks = {
        "supplier_notice_unreleased": lambda c: c["supplier_notices_not_released"] > 0,
        "retainage_slip": lambda c: c["lines_withholding_less_than_contract_retainage"],
        "expired_wc": lambda c: c["workers_comp_expired_by_period_end"],
        "draw_not_cleared": lambda c: not c["owner_draw_received_for_period"],
        "past_sov_no_co": lambda c: c["lines_billed_past_scheduled_value_plus_approved_change_orders"],
    }
    for data, label in anomaly_cases:
        if label.anomaly in checks:
            c = compute_facts(*state_for(data, label.request_id))["contract"]
            assert checks[label.anomaly](c), (label.anomaly, label.request_id, c)


def test_one_cent_shift_still_reads_as_a_resubmission(anomaly_cases):
    data, label = case(anomaly_cases, "duplicate_new_number")
    req = next(r for r in data["pay_requests"] if r.id == label.request_id)
    nudged = [l.model_copy(update={"total_to_date": l.total_to_date + D("0.01")}) for l in req.lines]
    dupes = compute_facts(*state_for(swap_request(data, req.id, lines=nudged), req.id))["duplicates"]
    assert dupes["earlier_requests"][0]["every_line_same_total_to_date"]


def test_clean_requests_do_not_look_like_duplicates(seed):
    for req in (r for r in seed["pay_requests"] if r.number == THIS_MONTH):
        dupes = compute_facts(*state_for(seed, req.id))["duplicates"]["earlier_requests"]
        assert not any(d["every_line_same_total_to_date"] for d in dupes), req.id


# --- email evidence


def _email(i, domain, sent, **claims):
    return Email(id=f"e{i}", from_addr=f"ap@{domain}", from_domain=domain, sent_at=sent, subject="Invoice",
                 body="Attached.", claims=EmailClaims(**claims))


def test_routine_mail_cannot_bury_a_bank_change_request(anomaly_cases):
    data, label = case(anomaly_cases, "bank_change_email")
    state, req = state_for(data, label.request_id)
    later = max(e.sent_at for e in state.emails) + timedelta(days=1)
    state.emails = state.emails + [_email(i, state.vendor.email_domain, later) for i in range(8)]
    bank = compute_facts(state, req)["bank"]
    assert any(e["asks_for_bank_change"] for e in bank["emails"]) and bank["emails_asking_for_bank_change"] >= 1


def test_mixed_timezones_do_not_crash(anomaly_cases):
    data, label = case(anomaly_cases, "bank_change_email")
    state, req = state_for(data, label.request_id)
    aware = datetime(2026, 10, 20, 12, tzinfo=timezone(timedelta(hours=-7)))
    state.emails = state.emails + [_email(99, state.vendor.email_domain, aware)]
    compute_facts(state, req)


@pytest.mark.parametrize("text", ["507.105.0724", "507/105/0724", "5071_0507_24", "507 \u2013 105 \u2013 0724",
                                  "5071\u2013050724", "50710\n50724", "447712782:5071050724"])
def test_account_numbers_masked_however_written(text):
    out = redact(f"pay {text} now")
    assert "0724" in out and not any(chunk in out for chunk in ("507105", "5071050", "50710", "105"))


def test_money_and_dates_survive_redaction():
    assert redact("$38,000.00 by 10/31/2026 or 2026-10-31") == "$38,000.00 by 10/31/2026 or 2026-10-31"


@pytest.mark.parametrize(
    "sender,why",
    [
        ("brightlinelectric.com", "one_or_two_letters_changed"),
        ("brightline-electric.com", "added_or_removed_hyphen"),
        ("brightlineelectric.co", "different_top_level_domain"),
        ("brightlineelectric.com.pay-portal.io", "vendor_name_in_subdomain"),
        ("br\u0456ghtlineelectric.com", "non_ascii_characters"),
        ("brightIineelectric.com", "confusable_characters"),
        ("BrightlineElectric.com", None),
        ("unrelated.com", None),
    ],
)
def test_imitation_domains(sender, why):
    assert domain_resembles(sender, "brightlineelectric.com") == why


# --- malformed model output never crashes judge() or passes as confident


@pytest.fixture
def clean(seed):
    req = next(r for r in seed["pay_requests"] if r.number == THIS_MONTH)
    return state_for(seed, req.id)


def ok_fallback(state, questions):
    return jev_answers(action="hold")


@pytest.mark.parametrize(
    "bad",
    [
        {ACTION: {"type": "choice", "choice": ["release"], "probabilities": {}, "confidence": 0.9}},
        {ACTION: {"type": "choice", "choice": "release", "probabilities": [0.9], "confidence": 0.9}},
        {ACTION: {"type": "choice", "choice": "release", "probabilities": {"release": "high"}, "confidence": 0.9}},
        {ACTION: {"type": "choice", "choice": "release", "probabilities": {"release": 0.05, "hold": 0.95},
                  "confidence": 0.97}},
        {BANK: {"type": "score", "score": 1, "legend": {"1": "a", "1.0": "b", "01": "c", "1.00": "d", "001": "e"},
                "probabilities": {}, "confidence": 0.9}},
        {BANK: {"type": "score", "score": True, "legend": {str(i): t for i, t in enumerate(QUESTIONS[BANK]["criteria"])},
                "probabilities": {}, "confidence": 0.9}},
        {BANK: {"type": "score", "score": 0, "probabilities": {}, "confidence": 0.9,
                "legend": {str(i): t for i, t in enumerate(reversed(QUESTIONS[BANK]["criteria"]))}}},
    ],
)
def test_malformed_jev_answers_fall_back(clean, bad):
    r = judge(*clean, ask_jev=lambda s, q: jev_answers(**bad), ask_fallback=ok_fallback)
    assert r.source == "claude", r.errors


def test_unexpected_exception_in_a_source_is_not_a_crash(clean):
    def explode(state, questions):
        raise KeyError("surprise")

    r = judge(*clean, ask_jev=explode, ask_fallback=explode)
    assert r.source == "unavailable" and "KeyError" in r.errors[0]


@pytest.fixture
def gateway_key(monkeypatch):
    monkeypatch.delenv("JEV_BASE_URL", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")


@pytest.mark.parametrize("body", [b"[1, 2]", b'{"code": 0, "data": []}', b"\xff\xfe not utf-8"])
def test_odd_jev_bodies_are_jev_errors(gateway_key, body):
    with pytest.raises(jev.JevError):
        jev.ask({}, {}, opener=lambda req, timeout: FakeResponse(body), sleep=lambda s: None)


@pytest.mark.parametrize("exc", [http.client.IncompleteRead(b""), http.client.RemoteDisconnected("bye"),
                                 ConnectionResetError("reset")])
def test_dropped_connections_are_jev_errors(gateway_key, exc):
    def opener(req, timeout):
        raise exc

    with pytest.raises(jev.JevError):
        jev.ask({}, {}, opener=opener, sleep=lambda s: None)


def test_redirects_are_refused():
    handler = jev._NoRedirect()
    req = jev.urllib.request.Request("https://ai-gateway.vercel.sh/typesafe/v1/systemone", data=b"{}")
    with pytest.raises(urllib.error.HTTPError, match="refusing redirect"):
        handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://evil.example/")


def test_plain_http_base_url_refused(monkeypatch):
    monkeypatch.setenv("JEV_BASE_URL", "http://ai-gateway.vercel.sh/typesafe")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")
    with pytest.raises(jev.JevError, match="https"):
        jev.ask({}, {})


@pytest.mark.parametrize(
    "probs",
    [
        {"release": 0.95, "hold": 0.6, "escalate": -0.55},  # sums to 1 with a negative
        {"release": 0.95},  # omitted options would be renormalized to certainty
        {"release": 0.8, "hold": 0.1, "escalate": 0.0},  # sums to 0.9
        {"release": 0.6, "hold": 0.3, "escalate": 0.1, "pay_twice": 0.0},  # unknown option
    ],
)
def test_fallback_cannot_manufacture_confidence(probs):
    with pytest.raises(fallback.FallbackError):
        fallback.ask({}, QUESTIONS, client=FakeClaude(claude_answers(**{ACTION: probs})))


def test_fallback_unparseable_output_is_a_fallback_error():
    import pydantic

    class Truncated:
        @property
        def beta(self):
            try:
                fallback.FallbackAnswers.model_validate_json('{"answers": [')
            except pydantic.ValidationError as e:
                err = e

            def parse(**kw):
                raise err

            return type("B", (), {"messages": type("M", (), {"parse": staticmethod(parse)})})()

    with pytest.raises(fallback.FallbackError, match="did not parse"):
        fallback.ask({}, QUESTIONS, client=Truncated())


def test_progress_facts_use_the_contracts_scheduled_value():
    """A sub that prints a bigger scheduled value so its % matches the video is still billing past the
    video: the facts Jev sees must say so (calibration: Jev was 0/40 on this attack before)."""
    import random

    import pipeline
    from judgment.facts import progress_facts
    from seed import anomalies as a
    from seed.generate import generate

    data, label = a.inflated_scheduled_value(generate(), "sub_02", random.Random(1), extra_points=25)
    ks, req = pipeline.kernel_state(data, label.request_id, pipeline.default_contract())
    f = progress_facts(ks, req)
    assert f["lines_billed_above_video_beyond_tolerance"], f
    inflated = next(l for l in f["lines"] if l["item_no"] in f["lines_billed_above_video_beyond_tolerance"])
    assert inflated["gap_exceeds_tolerance"] and inflated["percent_column_disagrees_with_billed"]
