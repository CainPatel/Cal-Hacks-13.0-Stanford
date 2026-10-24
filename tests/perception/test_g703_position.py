"""G703s whose line items aren't in a ruled table (spreadsheet exports): read by position under the
A..I column letters. The sheet below copies the shape of a real exported continuation sheet: blank
cells instead of $0.00 in some columns, $0.00 placeholder rows, an item-numbered grand total, an
application number printed as "Draw #1" and a dotted date."""
from datetime import datetime
from decimal import Decimal

import pytest
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

from perception.g703 import ExtractionError, _read_text_layer, _to_line_items, check_consistency, extract_pay_request

# x of each column's right edge (amounts are right-aligned under their letter), as on the export
LETTERS = {"A": 36, "B": 143, "C": 275, "D": 342, "E": 402, "F": 450, "G": 526, "H": 613, "I": 671}
RIGHT = {"C": 304, "D": 370, "E": 426, "F": 465, "G": 526, "%": 580, "H": 639, "I": 694}

ROWS = [  # item, description, {column: printed}
    ("1", "00 - Contract Requirements", {"D": "$0.00", "E": "$0.00", "F": "$0.00", "I": "$0.00"}),
    ("2", "007316.13 - Builder's Risk", {"C": "$81,851.00", "D": "$0.00", "E": "$81,851.00", "F": "$0.00",
                                         "G": "$81,851.00", "%": "100.00%", "H": "$0.00", "I": "$0.00"}),
    ("3", "007316.16 - Commercial Liability", {"C": "$14,763.00", "D": "$0.00", "E": "$14,736.00", "F": "$0.00",
                                               "G": "$14,736.00", "%": "99.82%", "H": "$27.00", "I": "$0.00"}),
    ("4", "007316.19 - Flood Insurance", {"C": "$8,000.00", "D": "$0.00", "E": "$0.00", "F": "$0.00",
                                          "H": "$8,000.00", "I": "$0.00"}),
    ("5", "142000 - Elevators", {"C": "$280,000.00", "D": "$0.00", "E": "$140,000.00", "F": "$0.00",
                                 "G": "$140,000.00", "%": "50.00%", "H": "$140,000.00", "I": "$14,000.00"}),
    ("6", "Total Project", {"C": "$384,614.00", "D": "$0.00", "E": "$236,587.00", "F": "$0.00", "G": "$236,587.00",
                            "%": "61.51%", "H": "$148,027.00", "I": "$14,000.00"}),
]


def build(path, *, remit=False, g702=False):
    c = canvas.Canvas(str(path), pagesize=landscape(letter))
    h = landscape(letter)[1]
    y = lambda top: h - top  # noqa: E731 - positions read like pdfplumber's "top"
    c.setFont("Helvetica", 7)
    c.drawString(28, y(45), "CONTINUATION SHEET AIA DOCUMENT G703 PAGE 1 OF 1 PAGES")
    c.drawString(513, y(56), "APPLICATION NO: Draw #1")
    c.drawString(537, y(76), "PERIOD TO: 11.25.2021")
    if remit:
        c.drawString(28, y(66), "REMIT TO ACCOUNT: 021000021-77812345")
    for letter_, x in LETTERS.items():
        c.drawCentredString(x, y(108), letter_)
    for text, x in [("ITEM", 27), ("DESCRIPTION OF WORK", 99), ("SCHEDULED", 252), ("WORK COMPLETED", 333),
                    ("MATERIALS", 428), ("TOTAL", 488), ("%", 553), ("BALANCE", 594), ("RETAINAGE", 650)]:
        c.drawString(x, y(120), text)
    for text, x in [("NO.", 30), ("VALUE", 262), ("FROM PREVIOUS", 310), ("THIS PERIOD", 378), ("(G ÷ C)", 544)]:
        c.drawString(x, y(130), text)
    top = 170
    for item, desc, cells in ROWS:
        c.drawString(37, y(top), item)
        c.drawString(47, y(top), desc)
        for col, text in cells.items():
            c.drawRightString(RIGHT[col], y(top), text)
        top += 10
    c.drawString(28, y(top + 20), "Users may obtain validation of this document by requesting a completed AIA Document D401")
    if g702:
        c.showPage()
        c.setFont("Helvetica", 9)
        for i, (label, amount) in enumerate([
                ("1. ORIGINAL CONTRACT SUM", "$384,614.00"), ("2. NET CHANGE BY CHANGE ORDERS", "$0.00"),
                ("3. CONTRACT SUM TO DATE", "$384,614.00"), ("4. TOTAL COMPLETED & STORED TO DATE", "$236,587.00"),
                ("TOTAL RETAINAGE", "$14,000.00"), ("6. TOTAL EARNED LESS RETAINAGE", "$222,587.00"),
                ("7. LESS PREVIOUS CERTIFICATES FOR PAYMENT", "$0.00"), ("8. CURRENT PAYMENT DUE", "$222,587.00")]):
            c.drawString(40, y(80 + 16 * i), f"{label} {amount}")
    c.save()
    return path


def test_line_items_are_read_by_column_position(tmp_path):
    sheet = _read_text_layer(build(tmp_path / "export.pdf"))
    lines, _ = _to_line_items(sheet)
    by_item = {li.item_no: li for li in lines}
    assert list(by_item) == ["2", "3", "4", "5"]  # the $0.00 placeholder row and the total are not lines
    flood = by_item["4"]  # G and % printed blank: they stay in their own columns, read as 0
    assert (flood.scheduled_value, flood.total_to_date, flood.pct_complete, flood.balance_to_finish) == \
        (Decimal("8000.00"), 0, 0, Decimal("8000.00"))
    assert by_item["3"].balance_to_finish == Decimal("27.00") and by_item["5"].retainage == Decimal("14000.00")
    assert by_item["2"].description == "007316.13 - Builder's Risk"  # the cost code is description, not item
    assert sheet.totals["scheduled_value"] == "$384,614.00"  # the numbered "Total Project" row
    assert not check_consistency(lines, sheet.totals)  # every column total agrees with the lines
    codes = [n.code for n in sheet.notes]
    assert "read_by_position" in codes and "section_heading" in codes and "after_total_ignored" in codes
    assert (sheet.application_no, sheet.period_to) == ("1", "11.25.2021")


def test_a_complete_export_becomes_a_pay_request(tmp_path):
    r = extract_pay_request(build(tmp_path / "export.pdf", remit=True, g702=True), sub_id="sub_02", job_id="job_1",
                            submitted_at=datetime(2021, 11, 26, 9), allow_llm_fallback=False)
    pr = r.pay_request
    assert r.method == "text_layer" and pr.number == 1 and str(pr.period_end) == "2021-11-25"
    assert pr.g702.current_payment_due == Decimal("222587.00") and len(pr.lines) == 4
    assert pr.bank_account == "021000021-77812345"


def test_what_the_kernel_still_needs_is_said_at_once(tmp_path):
    with pytest.raises(ExtractionError, match=r"read 4 line items, but: no remit-to bank account.*; no G702"):
        extract_pay_request(build(tmp_path / "export.pdf"), sub_id="s", job_id="j",
                            submitted_at=datetime(2021, 11, 26), allow_llm_fallback=False)


def test_a_line_named_total_is_still_a_line(tmp_path):
    """Only a row whose scheduled value is the sum of the lines above closes the sheet."""
    from perception.g703 import RawRow, _numbered_grand_total
    rows = [RawRow("1", "a", {"scheduled_value": "100.00"}), RawRow("2", "b", {"scheduled_value": "50.00"})]
    assert _numbered_grand_total("Total Project", {"scheduled_value": "$150.00"}, rows)
    assert not _numbered_grand_total("Total station rental", {"scheduled_value": "$40.00"}, rows)
    assert not _numbered_grand_total("Elevators", {"scheduled_value": "$150.00"}, rows)


def test_claude_placeholder_row_is_skipped_not_a_line(tmp_path, fake_claude):
    """A scan goes to Claude; a row it transcribes with $0.00 everywhere and no scheduled value is a
    heading, not a line with a missing contract amount."""
    from tests.perception.g703_builder import build_g703, rasterize, standard_lines
    from tests.perception.test_g703 import _row, _transcription
    scanned = rasterize(build_g703(tmp_path / "a.pdf", standard_lines()), tmp_path / "scan.pdf")
    rows = [_row("1", "00 - Contract Requirements", "", "$0.00", "$0.00", "$0.00", "", "", "", "$0.00"),
            *_transcription().rows]
    r = extract_pay_request(scanned, sub_id="s", job_id="j", submitted_at=datetime(2026, 10, 1),
                            allow_llm_fallback=True, client=fake_claude(_transcription(rows=rows)))
    assert r.method == "claude" and [li.description for li in r.pay_request.lines] == ["General conditions"]
    assert any(n.code == "section_heading" for n in r.notes)
