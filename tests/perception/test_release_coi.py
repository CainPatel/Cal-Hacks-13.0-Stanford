from datetime import date
from decimal import Decimal

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from perception import llm
from perception.coi import NotACOI, extract_coi
from perception.common import ExtractionError
from perception.release import NotARelease, extract_release
from shared.schema import ReleaseKind
from tests.perception.g703_builder import rasterize

CONDITIONAL = ("This document waives and releases lien rights ... This document is effective only on the claimant's "
               "receipt of payment from the financial institution on which the following check is drawn.")
PAID = "This document waives and releases lien rights ... The claimant has been paid in full for the amount below."


def release_pdf(path, *, title="CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT", body=CONDITIONAL,
                through="10/31/2026", amount="$54,000.00", signature="/s/ Coastal Concrete Inc.",
                claimant="Coastal Concrete Inc.", overlay=None):
    import textwrap

    c = canvas.Canvas(str(path), pagesize=letter)
    y = 720
    lines = [title, "Identifying Information", f"Name of Claimant: {claimant}",
             "Name of Customer: Ridgeline Builders, Inc.", f"Through Date: {through}", "Waiver and Release",
             *textwrap.wrap(body, 95), f"Amount of Payment: {amount}", f"Claimant's Signature: {signature}",
             "Date of Signature: 11/01/2026"]
    for line in lines:
        c.setFont("Helvetica", 9)
        c.drawString(72, y, line)
        y -= 22
    c.amount_y = 720 - 22 * lines.index(f"Amount of Payment: {amount}")
    if overlay:
        overlay(c)
    c.save()
    return path


def read_release(path, **kw):
    kw.setdefault("allow_llm_fallback", False)
    return extract_release(path, sub_id="sub_01", job_id="job_1", received_at=date(2026, 11, 2), **kw)


@pytest.mark.parametrize("title,kind", [
    ("CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT", ReleaseKind.conditional_progress),
    ("UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT", ReleaseKind.unconditional_progress),
    ("CONDITIONAL WAIVER AND RELEASE ON FINAL PAYMENT", ReleaseKind.conditional_final),
    ("UNCONDITIONAL WAIVER AND RELEASE UPON FINAL PAYMENT", ReleaseKind.unconditional_final),
])
def test_release_kinds(tmp_path, title, kind):
    body = PAID if title.startswith("UN") else CONDITIONAL
    r = read_release(release_pdf(tmp_path / "r.pdf", title=title, body=body))
    rel = r.record
    assert rel.kind == kind and rel.through_date == date(2026, 10, 31) and rel.amount == Decimal("54000.00")
    assert rel.signed and (rel.sub_id, rel.job_id, rel.received_at) == ("sub_01", "job_1", date(2026, 11, 2))
    assert r.notes == []


def test_unsigned_release(tmp_path):
    assert read_release(release_pdf(tmp_path / "r.pdf", signature="______________________")).record.signed is False


def test_title_and_body_disagree(tmp_path):
    r = read_release(release_pdf(tmp_path / "r.pdf", title="UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT"))
    assert "title_body_mismatch" in {n.code for n in r.notes}


def test_claimant_mismatch_noted(tmp_path):
    r = read_release(release_pdf(tmp_path / "r.pdf"), expected_claimant="Pacific Roofing LLC")
    assert "claimant_mismatch" in {n.code for n in r.notes}


def test_two_different_titles_is_an_error(tmp_path):
    with pytest.raises(ExtractionError, match="more than one release title"):
        read_release(release_pdf(tmp_path / "r.pdf", claimant="UNCONDITIONAL WAIVER AND RELEASE ON FINAL PAYMENT"))


def test_hidden_digit_cannot_change_the_amount(tmp_path):
    def overlay(c):
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica", 9)
        c.drawString(72 + c.stringWidth("Amount of Payment: $54,000.00", "Helvetica", 9) + 0.5, c.amount_y, "9")

    r = read_release(release_pdf(tmp_path / "r.pdf", overlay=overlay))
    assert r.record.amount == Decimal("54000.00") and "hidden_text" in {n.code for n in r.notes}


class FakeClaude:
    def __init__(self, parsed):
        from types import SimpleNamespace
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=lambda **kw: SimpleNamespace(
            stop_reason="end_turn", parsed_output=parsed, _request_id="req_test")))


def test_scanned_release_goes_to_claude(tmp_path):
    scan = rasterize(release_pdf(tmp_path / "r.pdf"), tmp_path / "scan.pdf")
    with pytest.raises(ExtractionError, match="scanned"):
        read_release(scan)
    t = llm.ReleaseTranscription(is_lien_release=True, title="Conditional Waiver and Release on Progress Payment",
                                 through_date="10/31/2026", amount="$54,000.00", claimant="Coastal Concrete Inc.",
                                 signature_present=False, unreadable_fields=[])
    r = read_release(scan, allow_llm_fallback=True, client=FakeClaude(t))
    assert r.method == "claude" and r.record.kind == ReleaseKind.conditional_progress and r.record.signed is False


def test_claude_says_not_a_release(tmp_path):
    scan = rasterize(release_pdf(tmp_path / "r.pdf"), tmp_path / "scan.pdf")
    t = llm.ReleaseTranscription(is_lien_release=False, title="Invoice", through_date="", amount="", claimant="",
                                 signature_present=False, unreadable_fields=[])
    with pytest.raises(NotARelease):
        read_release(scan, allow_llm_fallback=True, client=FakeClaude(t))


# --- certificates of insurance ------------------------------------------------


def coi_pdf(path, rows, statement="Certificate holder is included as additional insured: YES",
            insured="Coastal Concrete Inc."):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 720
    for line in ["CERTIFICATE OF LIABILITY INSURANCE", f"INSURED: {insured}",
                 "TYPE OF INSURANCE   POLICY NUMBER   POLICY EFF   POLICY EXP", *rows, statement]:
        c.setFont("Helvetica", 9)
        c.drawString(72, y, line)
        y -= 22
    c.save()
    return path


ROWS = ["COMMERCIAL GENERAL LIABILITY  GL-0001  01/01/2026  01/01/2027",
        "WORKERS COMPENSATION AND EMPLOYERS' LIABILITY  WC-0001  03/01/2026  09/30/2026"]


def read_coi(path, **kw):
    kw.setdefault("allow_llm_fallback", False)
    return extract_coi(path, sub_id="sub_01", **kw)


def test_acord_style_rows_take_the_expiration(tmp_path):
    coi = read_coi(coi_pdf(tmp_path / "c.pdf", ROWS)).record
    assert (coi.gl_expiry, coi.wc_expiry, coi.additional_insured) == (date(2027, 1, 1), date(2026, 9, 30), True)


@pytest.mark.parametrize("statement,expected", [
    ("Certificate holder is included as additional insured: NO", False),
    ("The certificate holder is named as an additional insured.", True),
    ("The certificate holder is not named as an additional insured.", False),
])
def test_additional_insured_statements(tmp_path, statement, expected):
    assert read_coi(coi_pdf(tmp_path / "c.pdf", ROWS, statement=statement)).record.additional_insured is expected


def test_unstated_additional_insured_is_never_guessed(tmp_path):
    with pytest.raises(ExtractionError, match="additional-insured"):
        read_coi(coi_pdf(tmp_path / "c.pdf", ROWS, statement="Thank you for your business."))


def test_contradictory_additional_insured_is_an_error(tmp_path):
    with pytest.raises(ExtractionError, match="grants and denies"):
        read_coi(coi_pdf(tmp_path / "c.pdf", ROWS, statement="Additional insured: YES. Holder is not an additional insured."))


def test_missing_workers_comp_row(tmp_path):
    with pytest.raises(ExtractionError, match="workers' comp"):
        read_coi(coi_pdf(tmp_path / "c.pdf", ROWS[:1]))


def test_insured_mismatch_noted(tmp_path):
    r = read_coi(coi_pdf(tmp_path / "c.pdf", ROWS), expected_insured="Pacific Roofing LLC")
    assert "insured_mismatch" in {n.code for n in r.notes}


def test_scanned_coi_goes_to_claude(tmp_path):
    scan = rasterize(coi_pdf(tmp_path / "c.pdf", ROWS), tmp_path / "scan.pdf")
    t = llm.COITranscription(is_certificate_of_insurance=True, insured="Coastal Concrete Inc.",
                             general_liability_expiry="01/01/2027", workers_comp_expiry="09/30/2026",
                             additional_insured="unclear", unreadable_fields=[])
    with pytest.raises(ExtractionError, match="unclear"):
        read_coi(scan, allow_llm_fallback=True, client=FakeClaude(t))
    t = t.model_copy(update={"additional_insured": "yes"})
    r = read_coi(scan, allow_llm_fallback=True, client=FakeClaude(t))
    assert r.method == "claude" and r.record.wc_expiry == date(2026, 9, 30) and r.record.additional_insured


def test_claude_says_not_a_coi(tmp_path):
    scan = rasterize(coi_pdf(tmp_path / "c.pdf", ROWS), tmp_path / "scan.pdf")
    t = llm.COITranscription(is_certificate_of_insurance=False, insured="", general_liability_expiry="",
                             workers_comp_expiry="", additional_insured="unclear", unreadable_fields=[])
    with pytest.raises(NotACOI):
        read_coi(scan, allow_llm_fallback=True, client=FakeClaude(t))
