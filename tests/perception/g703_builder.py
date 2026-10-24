"""Renders synthetic G703 continuation sheets for perception tests.

Run directly to write sample PDFs to tests/fixtures/perception/:
    python -m tests.perception.g703_builder
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=7, leading=8)
_HEAD = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=6, leading=7, alignment=1)


@dataclass
class Line:
    item_no: str
    description: str
    scheduled: Decimal
    previous: Decimal
    this_period: Decimal
    stored: Decimal = Decimal("0")
    # Overrides let a test print values that disagree with the arithmetic.
    total_override: Decimal | None = None
    pct_text: str | None = None

    @property
    def total(self) -> Decimal:
        return self.total_override if self.total_override is not None else self.previous + self.this_period + self.stored


G702_LINES = [
    ("1. ORIGINAL CONTRACT SUM", "original_contract_sum"),
    ("2. NET CHANGE BY CHANGE ORDERS", "net_change_orders"),
    ("3. CONTRACT SUM TO DATE (Line 1 + 2)", "contract_sum_to_date"),
    ("4. TOTAL COMPLETED & STORED TO DATE", "total_completed_stored"),
    ("5. RETAINAGE", "retainage"),
    ("6. TOTAL EARNED LESS RETAINAGE (Line 4 - 5)", "total_earned_less_retainage"),
    ("7. LESS PREVIOUS CERTIFICATES FOR PAYMENT", "less_previous_certificates"),
    ("8. CURRENT PAYMENT DUE", "current_payment_due"),
]


def money_q(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"))


def money(d: Decimal) -> str:
    return f"{d:,.2f}" if d >= 0 else f"({-d:,.2f})"


def standard_lines() -> list[Line]:
    D = Decimal
    return [
        Line("1", "General conditions", D("40000"), D("16000"), D("4000")),
        Line("2", "Excavation and grading", D("85000"), D("85000"), D("0")),
        Line("3", "Concrete foundations", D("100000"), D("40000"), D("20000")),
        Line("4", "Framing", D("150000"), D("0"), D("30000"), D("12500")),
        Line("5", "Roofing", D("60000"), D("0"), D("0")),
    ]


def build_g703(
    path: Path,
    lines: list[Line],
    *,
    period_to: str = "10/31/2026",
    application_no: str = "3",
    retainage_rate: Decimal | None = Decimal("0.10"),
    totals_override: dict[str, Decimal] | None = None,
    extra_rows: list[list] | None = None,
    repeat_header: bool = True,
    grid: bool = True,
    overlay=None,
    period_to_later_pages: str | None = None,
    remit_account: str | None = "121000248:4417700921",
    g702: bool = True,
    g702_override: dict[str, Decimal] | None = None,
    previous_certificates: Decimal | None = None,
    aia_g702_page: bool = False,
) -> Path:
    """extra_rows are inserted after the line items, before the grand total, as raw cell lists.

    overlay(canvas) draws extra marks on page 1 after the page header, for tampering tests.
    """
    has_ret = retainage_rate is not None
    head1 = ["ITEM NO.", "DESCRIPTION OF WORK", "SCHEDULED VALUE", "WORK COMPLETED", "",
             "MATERIALS PRESENTLY STORED (NOT IN D OR E)", "TOTAL COMPLETED AND STORED TO DATE (D+E+F)",
             "% (G ÷ C)", "BALANCE TO FINISH (C - G)"]
    head2 = ["", "", "", "FROM PREVIOUS APPLICATION (D + E)", "THIS PERIOD", "", "", "", ""]
    letters = ["A", "B", "C", "D", "E", "F", "G", "G ÷ C", "H"]
    if has_ret:
        head1.append("RETAINAGE (IF VARIABLE RATE)")
        head2.append("")
        letters.append("I")

    data = [[Paragraph(h, _HEAD) if h else "" for h in head1], [Paragraph(h, _HEAD) if h else "" for h in head2], letters]
    sums = {k: Decimal("0") for k in ("C", "D", "E", "F", "G", "H", "I")}
    for li in lines:
        g = li.total
        h = li.scheduled - g
        pct = li.pct_text if li.pct_text is not None else f"{(g / li.scheduled * 100) if li.scheduled else 0:.2f}%"
        row = [li.item_no, Paragraph(li.description, _CELL), money(li.scheduled), money(li.previous),
               money(li.this_period), money(li.stored), money(g), pct, money(h)]
        ret = (g * retainage_rate).quantize(Decimal("0.01")) if has_ret else Decimal("0")
        if has_ret:
            row.append(money(ret))
        data.append(row)
        for k, v in zip("CDEFGHI", (li.scheduled, li.previous, li.this_period, li.stored, g, h, ret)):
            sums[k] += v
    for extra in extra_rows or []:
        data.append(extra + [""] * (len(letters) - len(extra)))
    sums.update(totals_override or {})
    total_row = ["", "GRAND TOTAL", money(sums["C"]), money(sums["D"]), money(sums["E"]), money(sums["F"]),
                 money(sums["G"]), f"{(sums['G'] / sums['C'] * 100) if sums['C'] else 0:.2f}%", money(sums["H"])]
    if has_ret:
        total_row.append(money(sums["I"]))
    data.append(total_row)

    widths = [0.5, 2.0, 0.9, 0.9, 0.8, 0.9, 0.9, 0.6, 0.9] + ([0.8] if has_ret else [])
    table = Table(data, colWidths=[w * inch for w in widths], repeatRows=3 if repeat_header else 0)
    table.setStyle(TableStyle([
        *([("GRID", (0, 0), (-1, -1), 0.5, colors.black)] if grid else []),
        ("SPAN", (3, 0), (4, 0)),
        ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (2, 0), (2, 1)),
        ("SPAN", (5, 0), (5, 1)), ("SPAN", (6, 0), (6, 1)), ("SPAN", (7, 0), (7, 1)), ("SPAN", (8, 0), (8, 1)),
        *([("SPAN", (9, 0), (9, 1))] if has_ret else []),
        ("FONTSIZE", (0, 2), (-1, -1), 7),
        ("ALIGN", (2, 3), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    def header(canvas, doc):
        if aia_g702_page and doc.page == 1:
            draw_aia_g702(canvas, first_page_values, application_no=application_no, period_to=period_to)
            return
        period = period_to_later_pages if (period_to_later_pages and doc.page > 1) else period_to
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(0.5 * inch, 7.9 * inch, "CONTINUATION SHEET  AIA DOCUMENT G703")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(
            0.5 * inch, 7.65 * inch,
            f"APPLICATION NO: {application_no}     APPLICATION DATE: 11/02/2026     PERIOD TO: {period}",
        )
        canvas.restoreState()
        if overlay and doc.page == 1:
            canvas.saveState()
            overlay(canvas)
            canvas.restoreState()

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(letter), topMargin=1.0 * inch,
                            leftMargin=0.5 * inch, rightMargin=0.5 * inch, bottomMargin=0.5 * inch)
    story = [Spacer(1, 0.1 * inch), table]
    if aia_g702_page:
        # Page 1 is an AIA-style G702 drawn by draw_aia_g702; the continuation sheet starts on page 2.
        story = [PageBreak()] + story
        g702 = False
    first_page_values = None
    if g702 or aia_g702_page:
        rate = retainage_rate or Decimal("0")
        prev = previous_certificates if previous_certificates is not None else money_q(sums["D"] * (1 - rate))
        values = {
            "original_contract_sum": sums["C"], "net_change_orders": Decimal("0"), "contract_sum_to_date": sums["C"],
            "total_completed_stored": sums["G"], "retainage": sums["I"],
            "total_earned_less_retainage": sums["G"] - sums["I"], "less_previous_certificates": prev,
            "current_payment_due": sums["G"] - sums["I"] - prev,
        }
        values.update(g702_override or {})
        if aia_g702_page:
            first_page_values = values
    if g702:
        summary = Table([[label, money(values[f])] for label, f in G702_LINES], colWidths=[3.2 * inch, 1.3 * inch])
        summary.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (1, 0), (1, -1), "RIGHT")]))
        story += [Spacer(1, 10), Paragraph("<b>APPLICATION SUMMARY (G702)</b>", _CELL), summary]
    if remit_account:
        story += [Spacer(1, 6), Paragraph(f"Remit payment to account: {remit_account}", _CELL)]
    doc.build(story, onFirstPage=header, onLaterPages=header)
    return path


AIA_LINES = [
    ("1. ORIGINAL CONTRACT SUM", "original_contract_sum"),
    ("2. NET CHANGE BY CHANGE ORDERS", "net_change_orders"),
    ("3. CONTRACT SUM TO DATE (Line 1 \u00b1 2)", "contract_sum_to_date"),
    ("4. TOTAL COMPLETED & STORED TO DATE (Column G on G703)", "total_completed_stored"),
    ("5. RETAINAGE:", None),
    ("   a. 10% of Completed Work (Columns D + E on G703)", "retainage"),
    ("   b. 0% of Stored Material (Column F on G703)", "zero"),
    ("   Total Retainage (Lines 5a + 5b, or Total in Column I of G703)", "retainage"),
    ("6. TOTAL EARNED LESS RETAINAGE (Line 4 minus Line 5 Total)", "total_earned_less_retainage"),
    ("7. LESS PREVIOUS CERTIFICATES FOR PAYMENT (Line 6 from prior Certificate)", "less_previous_certificates"),
    ("8. CURRENT PAYMENT DUE", "current_payment_due"),
    ("9. BALANCE TO FINISH, INCLUDING RETAINAGE (Line 3 minus Line 6)", "balance"),
]
RIGHT_COLUMN = [
    "ARCHITECT'S CERTIFICATE FOR PAYMENT",
    "In accordance with the Contract Documents, based on evaluation",
    "of the Work and the data comprising this application, the",
    "Architect certifies to the Owner that the Work has progressed",
    "AMOUNT CERTIFIED ........................ $ 1,000,000.00",
    "CHANGE ORDER SUMMARY      ADDITIONS      DEDUCTIONS",
    "Total changes approved in previous months  $ 0.00   $ 0.00",
    "Total approved this month                  $ 0.00   $ 0.00",
    "NET CHANGES by Change Order                $ 0.00",
    "Contractor: By ___________ Date: __________",
    "State of: ______  County of: ______",
    "Notary Public: ____________________",
]


def draw_aia_g702(c, values, *, application_no, period_to):
    """A two-column AIA G702 page: lines 1-9 on the left, certificate text on the same baselines on the right."""
    w, h = landscape(letter)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.5 * inch, h - 0.6 * inch, "APPLICATION AND CERTIFICATE FOR PAYMENT  AIA DOCUMENT G702")
    c.setFont("Helvetica", 8)
    c.drawString(w / 2 + 0.3 * inch, h - 0.85 * inch, f"APPLICATION NO: {application_no}")
    c.drawString(w / 2 + 0.3 * inch, h - 1.0 * inch, f"PERIOD TO: {period_to}")
    c.drawString(0.5 * inch, h - 1.3 * inch, "CONTRACTOR'S APPLICATION FOR PAYMENT")
    y = h - 1.6 * inch
    extra = {"zero": Decimal("0"), "balance": values["contract_sum_to_date"] - values["total_earned_less_retainage"]}
    for i, (label, field_) in enumerate(AIA_LINES):
        c.drawString(0.5 * inch, y, label)
        if field_:
            amount = values.get(field_, extra.get(field_))
            c.drawRightString(w / 2 - 0.2 * inch, y, f"$ {money(amount)}")
        if i < len(RIGHT_COLUMN):
            c.drawString(w / 2 + 0.3 * inch, y, RIGHT_COLUMN[i])
        y -= 0.3 * inch


def build_aia_g702(path: Path, lines: list[Line], *, application_no: str = "3", period_to: str = "10/31/2026",
                   retainage_rate: Decimal = Decimal("0.10"), override: dict[str, Decimal] | None = None) -> Path:
    """A G702 on its own, as a sub might send it separately from the G703."""
    from reportlab.pdfgen import canvas as rl_canvas

    completed = sum((l.total for l in lines), Decimal("0"))
    contract = sum((l.scheduled for l in lines), Decimal("0"))
    retainage = sum(((l.total * retainage_rate).quantize(Decimal("0.01")) for l in lines), Decimal("0"))
    prev = money_q(sum((l.previous for l in lines), Decimal("0")) * (1 - retainage_rate))
    values = {"original_contract_sum": contract, "net_change_orders": Decimal("0"), "contract_sum_to_date": contract,
              "total_completed_stored": completed, "retainage": retainage,
              "total_earned_less_retainage": completed - retainage, "less_previous_certificates": prev,
              "current_payment_due": completed - retainage - prev, **(override or {})}
    c = rl_canvas.Canvas(str(path), pagesize=landscape(letter))
    draw_aia_g702(c, values, application_no=application_no, period_to=period_to)
    c.save()
    return path


def rasterize(src: Path, dst: Path, resolution: int = 110) -> Path:
    """Write an image-only copy of src, like a scanned paper pay app."""
    import pdfplumber

    with pdfplumber.open(src) as pdf:
        images = [p.to_image(resolution=resolution).original.convert("RGB") for p in pdf.pages]
    images[0].save(dst, "PDF", save_all=True, append_images=images[1:])
    return dst


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "fixtures" / "perception"
    build_g703(out / "g703_clean.pdf", standard_lines())
    rasterize(out / "g703_clean.pdf", out / "g703_scanned.pdf")
    print(f"wrote fixtures to {out}")
