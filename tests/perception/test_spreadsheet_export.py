"""Pay applications exported from a spreadsheet, as they arrive in the AP inbox: a G703 printed
shrunk to fit one page (2 pt type), with the workbook's working columns printed right of column I
and item numbers a fraction of a space from the description; a ruled box around the header only;
and a G702 sent as its own file, in the AIA spreadsheet layout whose labels wrap onto a second line."""
from datetime import datetime
from decimal import Decimal

import pdfplumber
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

from perception import attachments
from perception.g703 import _find_header, _read_text_layer, _to_line_items, extract_pay_request, read_g702
from tests.perception.test_g703_position import LETTERS, RIGHT, ROWS

W, H = landscape(letter)
SHRUNK = 0.28  # a 7 pt sheet printed at 28% sets 2 pt type, like a browser's "fit to page"


def export_g703(path, *, rows=ROWS, period="11.25.2021", scale=1.0, working_column=False, ruled_header=False,
                tight_items=False, remit=True):
    c = canvas.Canvas(str(path), pagesize=(W, H))
    c.translate(0, H)
    c.scale(scale, scale)
    c.translate(0, -H)  # shrink toward the top-left corner, as a print to fit does
    y = lambda top: H - top  # noqa: E731
    c.setFont("Helvetica", 7)
    c.drawString(28, y(45), "CONTINUATION SHEET AIA DOCUMENT G703 PAGE 1 OF 1 PAGES")
    c.drawString(28, y(56), "AIA Document G702, APPLICATION AND CERTIFICATION FOR PAYMENT, containing")
    c.drawString(513, y(56), "APPLICATION NO: Draw #1")
    c.drawString(537, y(76), f"PERIOD TO: {period}")
    if remit:
        c.drawString(28, y(66), "Remit payment to account: 021000021:77812345")
    for letter_, x in LETTERS.items():
        c.drawCentredString(x, y(108), letter_)
    for text, x in [("ITEM", 27), ("DESCRIPTION OF WORK", 99), ("SCHEDULED", 252), ("WORK COMPLETED", 333),
                    ("MATERIALS", 428), ("TOTAL", 488), ("%", 553), ("BALANCE", 594)]:
        c.drawString(x, y(120), text)
    for text, x in [("NO.", 30), ("VALUE", 262), ("FROM PREVIOUS", 310), ("THIS PERIOD", 378), ("(G ÷ C)", 544)]:
        c.drawString(x, y(130), text)
    if ruled_header:  # borders around the letter row and header only; the lines below are borderless
        edges = [20, 45, 245, 305, 372, 427, 470, 530, 585, 640, 700]
        for x in edges:
            c.line(x, y(100), x, y(140))
        for top in (100, 112, 140):
            c.line(edges[0], y(top), edges[-1], y(top))
    if working_column:
        c.drawRightString(760, y(130), "actual exp")
    top = 170
    for item, desc, cells in rows:
        if tight_items:
            c.drawRightString(45.6, y(top), item)  # 0.2 em before the description, as on the real export
        else:
            c.drawString(37, y(top), item)
        c.drawString(47, y(top), desc)
        for col, text in cells.items():
            c.drawRightString(RIGHT[col], y(top), text)
        if working_column:
            c.drawRightString(760, y(top), cells.get("G", "$0.00"))
        top += 10
    c.save()
    return path


# Lines 1-9 of the AIA spreadsheet G702: (label, which amount, the line below it). The architect's
# certificate starts left of the page's middle, so cutting the page in half cuts its words.
G702_ROWS = [
    ("1.  ORIGINAL CONTRACT SUM", "$ {contract}", None),
    ("2.  Net change by Change Orders", "$ 0.00", None),
    ("3.  CONTRACT SUM TO DATE (Line 1 ± 2)", "$ {contract}", None),
    ("4.  TOTAL COMPLETED & STORED TO", "$ ${completed}", "         DATE       (Column G on G703)"),
    ("5.  RETAINAGE:", None, None),
    ("a.   % of Completed Work", "$ {retainage}", "     (Column D + E on G703)"),
    ("b.   % of Stored Material", "$", "     (Column F on G703)"),
    ("     Total Retainage (Lines 5a + 5b or", None, None),
    ("     Total in Column I of G703)", "$ {retainage}", None),
    ("6.  TOTAL EARNED LESS RETAINAGE", "$ {earned}", "     (Line 4 Less Line 5 Total)"),
    ("7.  LESS PREVIOUS CERTIFICATES FOR", None, None),
    ("     PAYMENT (Line 6 from prior Certificate)", "$ 0.00", None),
    ("8.  CURRENT PAYMENT DUE", "$ {earned}", None),
    ("9.  BALANCE TO FINISH, INCLUDING RETAINAGE", "$ {balance}", "     (Line 3 less Line 6)"),
]
G702_AMOUNTS = {"contract": "384,614.00", "completed": "236,587.00", "retainage": "14,000.00",
                "earned": "222,587.00", "balance": "162,027.00"}  # the ROWS sheet's totals
CERTIFICATE = ["CONTRACTOR:", "By: Date:", "State of: FLORIDA County of: PINELLAS",
               "ARCHITECT'S CERTIFICATE FOR PAYMENT", "In accordance with the Contract Documents, based on",
               "comprising the application, the Architect certifies", "Architect's knowledge, information and belief",
               "the quality of the Work is in accordance with", "is entitled to payment of the AMOUNT CERTIFIED."]


def export_g702(path, *, amounts=G702_AMOUNTS, period="11.25.2021"):
    c = canvas.Canvas(str(path), pagesize=(W, H))
    c.setFont("Helvetica", 8)
    c.drawString(36, H - 40, "APPLICATION AND CERTIFICATION FOR PAYMENT   AIA DOCUMENT G702   PAGE 1 of 1")
    c.drawString(430, H - 56, "APPLICATION NO: #01")
    c.drawString(430, H - 70, f"PERIOD TO: {period}")
    c.drawString(36, H - 90, "Continuation Sheet, AIA Document G703, is attached.")
    top, right = 110, iter(CERTIFICATE)
    for label, amount, below in G702_ROWS:
        c.drawString(36, H - top, label)
        if amount:
            c.drawRightString(330, H - top, amount.format(**amounts))
        c.drawString(W / 2 - 20, H - top, next(right, ""))
        top += 12
        if below:
            c.drawString(36, H - top, below)
            top += 12
    c.drawString(W / 2 - 20, H - top - 20, "(Initial all figures on this Application and on the")
    c.drawString(W / 2 - 20, H - top - 32, "Continuation Sheet that are changed to conform with the amount certified.)")
    c.save()
    return path


def lines_of(path):
    sheet = _read_text_layer(path)
    return sheet, {li.item_no: li for li in _to_line_items(sheet)[0]}


def test_a_shrunk_export_reads_exactly_like_the_full_size_one(tmp_path):
    full = lines_of(export_g703(tmp_path / "full.pdf"))[1]
    sheet, shrunk = lines_of(export_g703(tmp_path / "shrunk.pdf", scale=SHRUNK, working_column=True, tight_items=True))
    assert shrunk == full and list(shrunk) == ["2", "3", "4", "5"]
    assert shrunk["5"].retainage == Decimal("14000.00") and shrunk["2"].retainage == 0  # not the working column
    assert shrunk["2"].description == "007316.13 - Builder's Risk"  # the item number didn't swallow the cost code
    assert sheet.totals["retainage"] == "$14,000.00" and (sheet.application_no, sheet.period_to) == ("1", "11.25.2021")
    codes = [n.code for n in sheet.notes]
    assert "outside_sheet" in codes and "hidden_text" not in codes  # 2 pt type is visible, just small


def test_a_ruled_box_around_the_header_only_still_reads_the_lines(tmp_path):
    path = export_g703(tmp_path / "boxed.pdf", ruled_header=True)
    with pdfplumber.open(path) as pdf:  # the scenario: a ruled table with the header and no lines
        assert any(_find_header(t) for t in pdf.pages[0].extract_tables() if t)
    assert list(lines_of(path)[1]) == ["2", "3", "4", "5"]


def test_a_spreadsheet_g702_reads_its_wrapped_labels(tmp_path):
    fields, header, _ = read_g702(export_g702(tmp_path / "g702.pdf"))
    assert fields == {"original_contract_sum": "384,614.00", "net_change_orders": "0.00",
                      "contract_sum_to_date": "384,614.00", "total_completed_stored": "236,587.00",
                      "retainage": "14,000.00", "total_earned_less_retainage": "222,587.00",
                      "less_previous_certificates": "0.00", "current_payment_due": "222,587.00"}
    assert header == {"application_no": "01", "period_to": "11.25.2021"}


def test_g702_amounts_take_the_first_amount_and_wrap_only_to_their_own_line():
    from perception.g703 import _g702_from_text
    fields, _ = _g702_from_text([
        "2. Net change by Change Orders -$4,506.00 CON",  # negative, then the architect's column
        "4. TOTAL COMPLETED & STORED TO DATE $ (1,000.00)",
        "7. LESS PREVIOUS CERTIFICATES FOR PAYMENT $",  # printed blank on a first application
        "8. CURRENT PAYMENT DUE $ 595,008.60 is en",
    ])
    assert fields == {"net_change_orders": "-$4,506.00", "total_completed_stored": "(1,000.00)",
                      "less_previous_certificates": "", "current_payment_due": "595,008.60"}


def test_a_shrunk_g703_and_its_separate_g702_become_one_pay_request(tmp_path):
    r = extract_pay_request(export_g703(tmp_path / "g703.pdf", scale=SHRUNK, working_column=True, tight_items=True),
                            sub_id="sub_02", job_id="job_1", submitted_at=datetime(2021, 11, 26, 9),
                            allow_llm_fallback=False, g702_pdf=export_g702(tmp_path / "g702.pdf"))
    pr = r.pay_request
    assert r.method == "text_layer" and pr.number == 1  # "Draw #1" on the G703, "#01" on the G702
    assert pr.g702.current_payment_due == Decimal("222587.00") and pr.bank_account == "021000021:77812345"
    assert not [n for n in r.notes if n.code.startswith("g702_")]  # the G702 agrees with the lines


def test_an_aia_g702_is_not_mistaken_for_its_continuation_sheet(tmp_path):
    """The G702's own wording names the continuation sheet twice; the G703 names the G702 once."""
    assert attachments.classify(export_g702(tmp_path / "g702.pdf")) == "g702"
    assert attachments.classify(export_g703(tmp_path / "g703.pdf", scale=SHRUNK)) == "pay_app"
