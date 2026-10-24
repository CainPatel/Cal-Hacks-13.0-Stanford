"""Render seed records as the documents a GC actually receives. Owner: B.

Perception (C) reads these back; truth.json next to each set is the exact answer.
Each document picks a number style the way real subs' spreadsheets differ: dollar signs or
not, dashes or blanks for zero, percents as 48% or 0.48. The values never change, only
how they're printed.
"""
from decimal import Decimal
from email.message import EmailMessage
from email.utils import format_datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from shared.schema import COI, Email, Job, PayRequest, Release, ReleaseKind, Vendor

GC_NAME = "Ridgeline Builders, Inc."  # the fictional GC; letterhead only, not a schema fact
GC_EMAIL = "ap@ridgelinebuilders.com"

STYLES = {
    "plain": dict(dollar="$", zero=None, pct="points"),        # $1,234.56   $0.00   48%
    "accounting": dict(dollar="", zero="-", pct="points2"),    # 1,234.56    -       48.00%
    "bare": dict(dollar="", zero="", pct="fraction"),          # 1234.56     (blank) 0.48
}

G703_HEADERS = [
    ("A", "ITEM NO."), ("B", "DESCRIPTION OF WORK"), ("C", "SCHEDULED VALUE"),
    ("D", "FROM PREVIOUS APPLICATION"), ("E", "THIS PERIOD"), ("F", "MATERIALS PRESENTLY STORED"),
    ("G", "TOTAL COMPLETED AND STORED TO DATE (D+E+F)"), ("", "% (G / C)"),
    ("H", "BALANCE TO FINISH (C - G)"), ("I", "RETAINAGE"),
]

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

RELEASE_TITLES = {
    ReleaseKind.conditional_progress: "CONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT",
    ReleaseKind.unconditional_progress: "UNCONDITIONAL WAIVER AND RELEASE ON PROGRESS PAYMENT",
    ReleaseKind.conditional_final: "CONDITIONAL WAIVER AND RELEASE ON FINAL PAYMENT",
    ReleaseKind.unconditional_final: "UNCONDITIONAL WAIVER AND RELEASE ON FINAL PAYMENT",
}


def money(d: Decimal, style: str) -> str:
    s = STYLES[style]
    if d == 0 and s["zero"] is not None:
        return s["zero"]
    text = f"{abs(d):.2f}" if style == "bare" else f"{abs(d):,.2f}"
    return f"({s['dollar']}{text})" if d < 0 else f"{s['dollar']}{text}"


def pct(p: Decimal, style: str) -> str:
    return {"points": f"{p}%", "points2": f"{p:.2f}%", "fraction": f"{p / 100:.2f}"}[STYLES[style]["pct"]]


def _line_cells(line, style):
    return [line.item_no, line.description, money(line.scheduled_value, style),
            money(line.previous_applications, style), money(line.this_period, style),
            money(line.stored_materials, style), money(line.total_to_date, style),
            pct(line.pct_complete, style), money(line.balance_to_finish, style), money(line.retainage, style)]


def g703_pdf(req: PayRequest, vendor: Vendor, job: Job, path, style: str) -> None:
    ss = getSampleStyleSheet()
    small = ss["BodyText"].clone("small", fontSize=7, leading=8)
    head = [
        Paragraph(f"<b>CONTINUATION SHEET (G703 format)</b> &nbsp; Application No. {req.number} &nbsp; "
                  f"Period To: {req.period_end:%m/%d/%Y}", ss["Heading4"]),
        Paragraph(f"Project: {job.name} &nbsp;&nbsp; Owner: {job.owner} &nbsp;&nbsp; Architect: {job.architect}", small),
        Paragraph(f"From Subcontractor: {vendor.legal_name} &nbsp;&nbsp; To Contractor: {GC_NAME} &nbsp;&nbsp; "
                  f"Submitted: {req.submitted_at:%m/%d/%Y}", small),
        Spacer(1, 6),
    ]
    header = [Paragraph(f"<b>{letter_}</b><br/>{text}", small) for letter_, text in G703_HEADERS]
    totals = ["", "GRAND TOTAL"] + [money(sum(getattr(l, f) for l in req.lines), style) for f in
                                    ("scheduled_value", "previous_applications", "this_period",
                                     "stored_materials", "total_to_date")] + [
        "", money(sum(l.balance_to_finish for l in req.lines), style), money(sum(l.retainage for l in req.lines), style)]
    rows = [header] + [_line_cells(l, style) for l in req.lines] + [totals]
    widths = [0.55, 1.7, 0.9, 0.95, 0.85, 0.85, 1.05, 0.55, 0.95, 0.8]
    table = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    g702 = Table([[label, money(getattr(req.g702, field), style)] for label, field in G702_LINES],
                 colWidths=[3.2 * inch, 1.3 * inch])
    g702.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                              ("LINEBELOW", (0, -2), (-1, -2), 0.6, colors.black),
                              ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    remit = Paragraph(f"Remit payment to account: {req.bank_account}", small)
    SimpleDocTemplate(str(path), pagesize=landscape(letter), leftMargin=0.4 * inch, rightMargin=0.4 * inch,
                      topMargin=0.4 * inch, bottomMargin=0.4 * inch, title=f"Pay application {req.id}").build(
        head + [table, Spacer(1, 10), Paragraph("<b>APPLICATION SUMMARY (G702)</b>", small), g702,
                Spacer(1, 6), remit])


def g703_xlsx(req: PayRequest, vendor: Vendor, job: Job, path) -> None:
    """The spreadsheet a sub emails: real numbers in cells, formatted like the PDF."""
    wb = Workbook()
    ws = wb.active
    ws.title = "G703"
    ws.append([f"CONTINUATION SHEET - Application No. {req.number} - Period To {req.period_end:%m/%d/%Y}"])
    ws.append([f"Project: {job.name}", "", f"Subcontractor: {vendor.legal_name}"])
    ws.append([])
    ws.append([f"{l} {t}".strip() for l, t in G703_HEADERS])
    for c in ws[4]:
        c.font, c.alignment = Font(bold=True), Alignment(wrap_text=True)
    for l in req.lines:
        ws.append([l.item_no, l.description, l.scheduled_value, l.previous_applications, l.this_period,
                   l.stored_materials, l.total_to_date, l.pct_complete / 100, l.balance_to_finish, l.retainage])
    for row in ws.iter_rows(min_row=5, max_row=ws.max_row):
        for c in row[2:]:
            c.number_format = '#,##0.00'
        row[7].number_format = '0.00%'
    ws.append([])
    for label, field in G702_LINES:
        ws.append([label, "", getattr(req.g702, field)])
        ws.cell(ws.max_row, 3).number_format = '#,##0.00'
    ws.append(["Remit payment to account:", "", req.bank_account])
    for col, width in zip("ABCDEFGHIJ", [9, 28, 14, 14, 14, 14, 16, 9, 14, 12]):
        ws.column_dimensions[col].width = width
    wb.save(path)


def release_pdf(rel: Release, vendor: Vendor, job: Job, path) -> None:
    ss = getSampleStyleSheet()
    conditional = rel.kind in (ReleaseKind.conditional_progress, ReleaseKind.conditional_final)
    body = [
        Paragraph(RELEASE_TITLES[rel.kind], ss["Heading3"]),
        Paragraph("<b>Identifying Information</b>", ss["BodyText"]),
        Paragraph(f"Name of Claimant: {vendor.legal_name}<br/>Name of Customer: {GC_NAME}<br/>"
                  f"Job Location: {job.name}<br/>Owner: {job.owner}<br/>Through Date: {rel.through_date:%m/%d/%Y}",
                  ss["BodyText"]),
        Spacer(1, 8),
        Paragraph("<b>Waiver and Release</b>", ss["BodyText"]),
        Paragraph("This document waives and releases lien, stop payment notice, and payment bond rights the "
                  "claimant has for labor and service provided, and equipment and material delivered, to the "
                  "customer on this job through the Through Date of this document."
                  + (" This document is effective only on the claimant's receipt of payment from the financial "
                     "institution on which the following check is drawn." if conditional else
                     " The claimant has been paid in full for the amount below."), ss["BodyText"]),
        Paragraph(f"Amount of Payment: ${rel.amount:,.2f}", ss["BodyText"]),
        Spacer(1, 18),
        Paragraph(f"Claimant's Signature: {'/s/ ' + vendor.legal_name if rel.signed else '______________________'}"
                  f"<br/>Date of Signature: {rel.received_at:%m/%d/%Y}", ss["BodyText"]),
    ]
    SimpleDocTemplate(str(path), pagesize=letter, title=f"Release {rel.id}").build(body)


def coi_pdf(coi: COI, vendor: Vendor, path) -> None:
    ss = getSampleStyleSheet()
    rows = [["TYPE OF INSURANCE", "POLICY NUMBER", "POLICY EXP"],
            ["Commercial General Liability", f"GL-{coi.sub_id.upper()}-26", f"{coi.gl_expiry:%m/%d/%Y}"],
            ["Workers Compensation and Employers' Liability", f"WC-{coi.sub_id.upper()}-26", f"{coi.wc_expiry:%m/%d/%Y}"]]
    table = Table(rows, colWidths=[3.2 * inch, 1.6 * inch, 1.2 * inch])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]))
    body = [
        Paragraph("CERTIFICATE OF LIABILITY INSURANCE", ss["Heading3"]),
        Paragraph(f"Insured: {vendor.legal_name}", ss["BodyText"]), Spacer(1, 6), table, Spacer(1, 8),
        Paragraph(f"Certificate Holder: {GC_NAME}. Certificate holder is included as additional insured: "
                  f"<b>{'YES' if coi.additional_insured else 'NO'}</b>", ss["BodyText"]),
    ]
    SimpleDocTemplate(str(path), pagesize=letter, title=f"COI {coi.id}").build(body)


def email_eml(email: Email, path) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = email.from_addr, GC_EMAIL, email.subject
    msg["Date"] = format_datetime(email.sent_at)
    msg.set_content(email.body)
    path.write_bytes(bytes(msg))
