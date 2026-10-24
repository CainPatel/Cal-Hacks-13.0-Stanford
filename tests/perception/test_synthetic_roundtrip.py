"""Every synthetic pay application B renders must read back exactly as its truth.json.

Skipped until synthetic/ (B) is merged. Perception's whole contract in one test:
the documents a GC receives, in every number style, become the records they came from.
"""
import json
from decimal import Decimal

import pytest

synthetic_main = pytest.importorskip("synthetic.__main__")

from perception import extract_pay_request  # noqa: E402
from shared.schema import PayRequest  # noqa: E402


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    synthetic_main.main(out)
    return sorted(out.glob("*/*/truth.json"))


def test_every_rendered_pay_app_reads_back_exactly(rendered):
    assert rendered
    for truth_path in rendered:
        truth = json.loads(truth_path.read_text())
        want = PayRequest.model_validate(truth["pay_request"])
        got = extract_pay_request(truth_path.parent / "g703.pdf", sub_id=want.sub_id, job_id=want.job_id,
                                  submitted_at=want.submitted_at, request_id=want.id,
                                  allow_llm_fallback=False).pay_request
        where = f"{truth_path.parent.name} [{truth['style']}]"
        assert (got.number, got.period_end, got.bank_account, got.g702) == \
               (want.number, want.period_end, want.bank_account, want.g702), where
        assert len(got.lines) == len(want.lines), where
        for g, w in zip(got.lines, want.lines):
            # The "bare" style prints percent as a 2-decimal fraction (0.48), so finer digits aren't on the page.
            pct_ok = abs(g.pct_complete - w.pct_complete) <= Decimal("0.5") if truth["style"] == "bare" \
                else g.pct_complete == w.pct_complete
            assert pct_ok and g.model_dump(exclude={"pct_complete"}) == w.model_dump(exclude={"pct_complete"}), \
                f"{where} line {w.item_no}"
