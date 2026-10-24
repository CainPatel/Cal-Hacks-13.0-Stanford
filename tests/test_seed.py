"""Clean seed must be internally consistent, or every rule fixture built on it lies."""
from collections import defaultdict

from seed.generate import generate
from shared.schema import LedgerKind, ReleaseKind

DATA = generate()


def test_counts():
    assert len(DATA["jobs"]) == 3
    assert len(DATA["vendors"]) == 15
    assert len(DATA["pay_requests"]) == 15 * 6
    assert DATA["labels"] == []  # clean: anomalies get planted later


def test_line_math_and_cumulative_continuity():
    last = defaultdict(dict)
    for r in sorted(DATA["pay_requests"], key=lambda r: (r.sub_id, r.number)):
        for l in r.lines:
            assert l.previous_applications == last[r.sub_id].get(l.item_no, 0)
            assert l.total_to_date == l.previous_applications + l.this_period + l.stored_materials
            assert 0 <= l.total_to_date <= l.scheduled_value
            assert l.balance_to_finish == l.scheduled_value - l.total_to_date
            last[r.sub_id][l.item_no] = l.total_to_date


def test_retainage_and_g702():
    certified = defaultdict(int)
    for r in sorted(DATA["pay_requests"], key=lambda r: (r.sub_id, r.number)):
        g = r.g702
        assert all(l.retainage == (l.total_to_date * 10 / 100).quantize(l.retainage) for l in r.lines)
        assert g.total_completed_stored == sum(l.total_to_date for l in r.lines)
        assert g.retainage == sum(l.retainage for l in r.lines)
        assert g.total_earned_less_retainage == g.total_completed_stored - g.retainage
        assert g.less_previous_certificates == certified[r.sub_id]
        assert g.current_payment_due == g.total_earned_less_retainage - g.less_previous_certificates
        assert g.current_payment_due >= 0
        certified[r.sub_id] = g.total_earned_less_retainage


def test_paperwork_present():
    rel = {(x.sub_id, x.kind, x.through_date) for x in DATA["releases"] if x.signed}
    paid = {e.request_id for e in DATA["ledger"] if e.kind == LedgerKind.sub_payment}
    draws = {(e.job_id, e.period_end) for e in DATA["ledger"] if e.kind == LedgerKind.owner_draw}
    for r in DATA["pay_requests"]:
        assert (r.sub_id, ReleaseKind.conditional_progress, r.period_end) in rel
        assert (r.job_id, r.period_end) in draws
        if r.number < 6:
            assert r.id in paid
            assert (r.sub_id, ReleaseKind.unconditional_progress, r.period_end) in rel
        else:
            assert r.id not in paid
    assert all(n.released for n in DATA["lower_tier_notices"])


def test_deterministic():
    assert generate() == DATA
