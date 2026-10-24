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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
) -> Path:
    """extra_rows are inserted after the line items, before the grand total, as raw cell lists."""
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
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("SPAN", (3, 0), (4, 0)),
        ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (2, 0), (2, 1)),
        ("SPAN", (5, 0), (5, 1)), ("SPAN", (6, 0), (6, 1)), ("SPAN", (7, 0), (7, 1)), ("SPAN", (8, 0), (8, 1)),
        *([("SPAN", (9, 0), (9, 1))] if has_ret else []),
        ("FONTSIZE", (0, 2), (-1, -1), 7),
        ("ALIGN", (2, 3), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    def header(canvas, doc):
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(0.5 * inch, 7.9 * inch, "CONTINUATION SHEET  AIA DOCUMENT G703")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(
            0.5 * inch, 7.65 * inch,
            f"APPLICATION NO: {application_no}     APPLICATION DATE: 11/02/2026     PERIOD TO: {period_to}",
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(letter), topMargin=1.0 * inch,
                            leftMargin=0.5 * inch, rightMargin=0.5 * inch, bottomMargin=0.5 * inch)
    doc.build([Spacer(1, 0.1 * inch), table], onFirstPage=header, onLaterPages=header)
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
