"""G703 continuation sheet -> PayRequest.

Two paths produce the same intermediate RawSheet of printed strings:
  1. the PDF's text layer, read with pdfplumber (exact, preferred)
  2. Claude transcription (only when the text layer is missing or unusable)
One parser turns RawSheet into LineItems, and one set of checks reports
inconsistencies. Checks never change values: a sub's arithmetic errors are
evidence for the kernel, so they are reported and passed through.
"""

import hashlib
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import anthropic
import numpy as np
import pdfplumber
import pdfplumber.utils.exceptions

from perception import llm
from perception.numbers import CellParseError, parse_date, parse_money, parse_pct
from perception.schema import LineItem, PayRequest

MONEY_COLUMNS = (
    "scheduled_value",
    "previous_applications",
    "this_period",
    "stored_materials",
    "total_to_date",
    "balance_to_finish",
    "retainage",
)
REQUIRED_COLUMNS = {
    "item_no",
    "description",
    "scheduled_value",
    "previous_applications",
    "this_period",
    "total_to_date",
    "pct_complete",
    "balance_to_finish",
}
MONEY_TOLERANCE = Decimal("0.01")
PCT_TOLERANCE = Decimal("0.5")  # printed percentages are usually rounded


class ExtractionError(RuntimeError):
    """The document could not be turned into a PayRequest by any path."""


class NotAG703(ExtractionError):
    pass


class _UseFallback(Exception):
    """Internal: the text layer can't be trusted for this document."""


@dataclass
class Note:
    code: str
    message: str
    item_no: str | None = None


@dataclass
class RawRow:
    item_no: str
    description: str
    cells: dict[str, str]  # column key -> printed text


@dataclass
class RawSheet:
    application_no: str
    application_date: str
    period_to: str
    has_retainage_column: bool
    rows: list[RawRow]
    totals: dict[str, str] | None
    notes: list[Note] = field(default_factory=list)


@dataclass
class ExtractionResult:
    pay_request: PayRequest
    method: str  # "text_layer" or "claude"
    notes: list[Note]
    sha256: str
    fallback_reason: str | None = None


# --- Text layer -------------------------------------------------------------


def _classify_header(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text.upper())
    # Order matters: G's header says "STORED TO DATE", D's says "(D + E)".
    if "RETAINAGE" in t:
        return "retainage"
    if "BALANCE" in t:
        return "balance_to_finish"
    if "%" in t or "PERCENT" in t:
        return "pct_complete"
    if "TOTAL" in t or "TO DATE" in t:
        return "total_to_date"
    if "MATERIALS" in t or "STORED" in t:
        return "stored_materials"
    if "THIS PERIOD" in t:
        return "this_period"
    if "PREVIOUS" in t:
        return "previous_applications"
    if "SCHEDULED" in t:
        return "scheduled_value"
    if "DESCRIPTION" in t:
        return "description"
    if "ITEM" in t:
        return "item_no"
    return None


def _find_header(table: list[list[str | None]]) -> tuple[dict[int, str], int] | None:
    """Return (column index -> key, number of header rows) if the table starts with a G703 header.

    Header rows are accumulated per column because AIA headers span rows
    ("WORK COMPLETED" over "FROM PREVIOUS APPLICATION" / "THIS PERIOD").
    """
    ncols = max(len(r) for r in table)
    acc = [""] * ncols
    for depth, row in enumerate(table[:5], start=1):
        for i, cell in enumerate(row):
            if cell:
                acc[i] += " " + cell
        mapping: dict[int, str] = {}
        seen: set[str] = set()
        ambiguous = False
        for i, text in enumerate(acc):
            key = _classify_header(text) if text.strip() else None
            if key is None:
                continue
            if key in seen:
                ambiguous = True
            seen.add(key)
            mapping[i] = key
        if REQUIRED_COLUMNS <= seen and not ambiguous:
            return mapping, depth
    return None


_LETTER_CELL = re.compile(r"^[A-I](\s*[÷/]\s*[A-I])?$|^%?\s*\(?[A-I]\s*[÷/]\s*[A-I]\)?$")


def _is_letter_row(cells: list[str]) -> bool:
    filled = [c for c in cells if c]
    return bool(filled) and all(_LETTER_CELL.match(c) for c in filled)


_INK_RESOLUTION = 144
_INK_CONTRAST = 60  # grey levels between darkest and lightest pixel inside a glyph's box
_MAX_HIDDEN_SHARE = 0.2


def _char_key(c: dict) -> tuple:
    return (c["text"], round(c["x0"], 2), round(c["top"], 2))


def _inked_char_keys(page) -> tuple[set[tuple], int]:
    """Keys of characters that actually show up when the page is rendered.

    A text layer can hold characters a person never sees: invisible render
    mode, white or near-white fill, zero alpha, or glyphs covered by a shape.
    Rendering the page and looking for ink inside each glyph's box catches all
    of these at once, so the parser reads what the GC sees on screen.
    """
    img = np.asarray(page.to_image(resolution=_INK_RESOLUTION).original.convert("L"), dtype=np.int16)
    scale = _INK_RESOLUTION / 72
    keep, hidden = set(), 0
    for c in page.chars:
        key = _char_key(c)
        if c["text"].isspace():
            keep.add(key)
            continue
        # Trim the box edges so ink bleeding in from a neighbouring glyph doesn't count.
        dx, dy = (c["x1"] - c["x0"]) * 0.15, (c["bottom"] - c["top"]) * 0.1
        x0, x1 = int((c["x0"] + dx) * scale), math.ceil((c["x1"] - dx) * scale)
        y0, y1 = int((c["top"] + dy) * scale), math.ceil((c["bottom"] - dy) * scale)
        crop = img[max(y0, 0):y1, max(x0, 0):x1]
        if crop.size and int(crop.max()) - int(crop.min()) >= _INK_CONTRAST and c.get("size", 12) >= 3:
            keep.add(key)
        else:
            hidden += 1
    return keep, hidden


_DATE_TOKEN = r"(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})"
_HEADER_PATTERNS = {
    "application_no": re.compile(r"APPLICATION\s+NO\.?\s*:?\s*([A-Z0-9\-]*\d[A-Z0-9\-]*)", re.I),
    "application_date": re.compile(r"APPLICATION\s+DATE\s*:?\s*" + _DATE_TOKEN, re.I),
    "period_to": re.compile(r"PERIOD\s+TO\s*:?\s*" + _DATE_TOKEN, re.I),
}
_ITEM_NO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-/ ]{0,14}[A-Za-z0-9.])?$")
_TOTAL_LABEL = re.compile(r"\b(SUB-?\s?)?TOTALS?\b", re.I)


@dataclass
class _SheetState:
    mapping: dict[int, str] | None = None
    ncols: int | None = None
    closed: bool = False  # set once the grand total row is read
    rows: list[RawRow] = field(default_factory=list)
    totals: dict[str, str] | None = None
    notes: list[Note] = field(default_factory=list)


def _read_text_layer(path: Path) -> RawSheet:
    st = _SheetState()
    header_values: dict[str, set[str]] = {k: set() for k in _HEADER_PATTERNS}

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            raw_chars = len(page.chars)
            area = float(page.width * page.height)
            image_area = sum(float(im["width"] * im["height"]) for im in page.images)
            if raw_chars == 0:
                if page.images:
                    raise _UseFallback(f"page {page_no} is a scanned image with no text layer")
                continue  # blank page
            if image_area > 0.5 * area:
                # Text over a page-sized image is usually an OCR layer, which can misread digits.
                raise _UseFallback(f"page {page_no} looks like a scan with an OCR text layer")

            keep, hidden = _inked_char_keys(page)
            if hidden:
                printable = sum(1 for c in page.chars if not c["text"].isspace())
                if hidden > _MAX_HIDDEN_SHARE * printable:
                    raise _UseFallback(f"page {page_no}: text layer does not match the rendered page")
                st.notes.append(Note("hidden_text", f"page {page_no}: ignored {hidden} characters that are not visible on the page"))
                page = page.filter(lambda o: o.get("object_type") != "char" or _char_key(o) in keep)

            text = page.extract_text() or ""
            for key, pattern in _HEADER_PATTERNS.items():
                m = pattern.search(text)
                if m:
                    header_values[key].add(m.group(1).strip())

            tables = [t for t in page.extract_tables() if t]
            if not tables:
                tables = [t for t in page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"}) if t]
            for index, table in enumerate(tables):
                width = max(len(r) for r in table)
                found = _find_header(table)
                if found:
                    if st.closed:
                        raise _UseFallback(f"page {page_no}: a second continuation sheet starts after the grand total")
                    st.mapping, skip = found
                    st.ncols = width
                    body = table[skip:]
                elif st.mapping is None or width != st.ncols:
                    continue
                elif st.closed:
                    st.notes.append(Note("extra_table_ignored", f"page {page_no}: table after the grand total ignored"))
                    continue
                elif index == 0:
                    body = table  # sheet continues onto this page without repeating its header
                    st.notes.append(Note("continuation_table", f"page {page_no}: headerless table read as a continuation"))
                else:
                    # A same-width table mid-page could be a change-order log or anything else.
                    raise _UseFallback(f"page {page_no}: unlabelled table with the sheet's column layout")
                _collect_rows(body, st, page_no)

    if st.mapping is None:
        raise _UseFallback("no G703 column header found in the text layer")
    if not st.rows:
        raise _UseFallback("header found but no line items parsed")
    if "stored_materials" not in st.mapping.values():
        st.notes.append(Note("no_stored_column", "sheet has no stored-materials column; read as $0"))

    resolved = {}
    for key, values in header_values.items():
        if len(values) > 1:
            if key == "period_to":
                # The billing period decides which releases and prior payments apply; never pick one.
                raise ExtractionError(f"pages disagree on the billing period: {sorted(values)}")
            st.notes.append(Note("header_mismatch", f"{key} differs between pages: {sorted(values)}"))
        resolved[key] = next(iter(values)) if len(values) == 1 else ""

    return RawSheet(
        application_no=resolved["application_no"],
        application_date=resolved["application_date"],
        period_to=resolved["period_to"],
        has_retainage_column="retainage" in st.mapping.values(),
        rows=st.rows,
        totals=st.totals,
        notes=st.notes,
    )


def _collect_rows(body, st: _SheetState, page_no: int) -> None:
    value_keys = [k for k in st.mapping.values() if k not in ("item_no", "description")]
    table_start = len(st.rows)
    for raw in body:
        if st.closed:
            if any((c or "").strip() for c in raw):
                st.notes.append(Note("after_total_ignored", f"page {page_no}: row after the grand total ignored"))
            continue
        cells = {key: (raw[i] or "").strip() if i < len(raw) else "" for i, key in st.mapping.items()}
        item = cells.get("item_no", "")
        desc = cells.get("description", "")
        values = {k: cells.get(k, "") for k in value_keys}
        no_values = all(v == "" for v in values.values())
        label = f"{item} {desc}"

        if not item and not desc and no_values:
            continue
        if _is_letter_row(list(cells.values())) or (_find_header([list(cells.values())]) is not None):
            continue  # repeated header or the A..I column-letter row
        if not no_values and _TOTAL_LABEL.search(label) and (not item or _TOTAL_LABEL.search(item)):
            upper = label.upper()
            if re.search(r"SUB-?\s?TOTAL", upper):
                st.notes.append(Note("subtotal_skipped", f"page {page_no}: skipped subtotal row {label.strip()!r}"))
            elif "PAGE" in upper or "CARRIED" in upper or "FORWARD" in upper:
                st.notes.append(Note("page_total_skipped", f"page {page_no}: skipped page total row {label.strip()!r}"))
            else:
                st.totals = values
                st.closed = True
            continue
        if not item and no_values:
            if len(st.rows) > table_start:
                st.rows[-1].description = f"{st.rows[-1].description} {desc}".replace("\n", " ").strip()
            else:
                st.notes.append(Note("orphan_text", f"page {page_no}: text not attached to a line item: {desc!r}"))
            continue
        if not item:
            raise _UseFallback(f"page {page_no}: row with amounts but no item number ({desc!r})")
        if not _ITEM_NO.match(item):
            raise _UseFallback(f"page {page_no}: implausible item number {item!r}")
        if no_values:
            st.notes.append(Note("section_heading", f"page {page_no}: skipped heading {item} {desc!r}", item))
            continue
        st.rows.append(RawRow(item_no=item, description=desc.replace("\n", " "), cells=values))


# --- Claude transcription ---------------------------------------------------


def _read_with_claude(pdf_bytes: bytes, client) -> RawSheet:
    t = llm.transcribe_g703(pdf_bytes, client)
    if not t.is_g703:
        raise NotAG703("document is not a G703 continuation sheet")
    if t.unreadable_cells:
        # A guessed digit is worse than no answer: someone has to look at the paper.
        raise ExtractionError(f"cells could not be read with certainty: {', '.join(t.unreadable_cells)}")
    for r in t.rows:
        if not _ITEM_NO.match(r.item_no.strip()) or _TOTAL_LABEL.search(r.item_no):
            raise ExtractionError(f"transcribed row has an implausible item number: {r.item_no!r}")

    def cells(r) -> dict[str, str]:
        d = {k: getattr(r, k) for k in (*MONEY_COLUMNS, "pct_complete")}
        if not t.has_retainage_column:
            d.pop("retainage")
        return d

    return RawSheet(
        application_no=t.application_no,
        application_date=t.application_date,
        period_to=t.period_to,
        has_retainage_column=t.has_retainage_column,
        rows=[RawRow(r.item_no.strip(), r.description.strip(), cells(r)) for r in t.rows],
        totals=cells(t.totals_row) if t.totals_row else None,
        notes=[],
    )


# --- Shared parsing and checks ----------------------------------------------


def _to_line_items(sheet: RawSheet) -> tuple[list[LineItem], list[Note]]:
    notes: list[Note] = []
    pct_raw = [(r, *parse_pct(r.cells.get("pct_complete"))) for r in sheet.rows]
    # Some sheets print 0.60 rather than 60%. Scale only when no cell carries a
    # % sign and every value is a plausible fraction.
    any_sign = any(sign for _, _, sign in pct_raw)
    fractional = False
    if not any_sign and all(v <= 1 for _, v, _ in pct_raw) and any(v > 0 for _, v, _ in pct_raw):
        err_points = err_fraction = Decimal("0")
        for r, v, _ in pct_raw:
            try:
                c = parse_money(r.cells.get("scheduled_value"), blank_is_zero=False)
                g = parse_money(r.cells.get("total_to_date"))
            except CellParseError:
                continue  # surfaced below when the row is parsed
            if c:
                gc = g / c * 100
                err_points += abs(v - gc)
                err_fraction += abs(v * 100 - gc)
        fractional = err_fraction < err_points
    if fractional:
        notes.append(Note("pct_scaled", "percent column printed as fractions; multiplied by 100"))

    lines = []
    for r, pct, _ in pct_raw:
        try:
            money = {
                k: parse_money(r.cells.get(k), blank_is_zero=(k != "scheduled_value"))
                for k in MONEY_COLUMNS
                if k != "retainage"
            }
            retainage = parse_money(r.cells.get("retainage")) if sheet.has_retainage_column else None
        except CellParseError as e:
            raise CellParseError(f"item {r.item_no}: {e}") from e
        lines.append(
            LineItem(
                item_no=r.item_no,
                description=r.description,
                pct_complete=pct * 100 if fractional else pct,
                retainage=retainage,
                **money,
            )
        )
    return lines, notes


def check_consistency(lines: list[LineItem], totals: dict[str, str] | None) -> list[Note]:
    """Report arithmetic the sheet itself gets wrong. Values are never altered."""
    notes: list[Note] = []
    seen: dict[str, int] = {}
    for li in lines:
        seen[li.item_no] = seen.get(li.item_no, 0) + 1
        g = li.previous_applications + li.this_period + li.stored_materials
        if abs(g - li.total_to_date) > MONEY_TOLERANCE:
            notes.append(Note("g_mismatch", f"total to date {li.total_to_date} but D+E+F = {g}", li.item_no))
        if abs((li.scheduled_value - li.total_to_date) - li.balance_to_finish) > MONEY_TOLERANCE:
            notes.append(Note("h_mismatch", f"balance {li.balance_to_finish} but C-G = {li.scheduled_value - li.total_to_date}", li.item_no))
        if li.scheduled_value:
            pct = li.total_to_date / li.scheduled_value * 100
            if abs(pct - li.pct_complete) > PCT_TOLERANCE:
                notes.append(Note("pct_mismatch", f"printed {li.pct_complete}% but G/C = {pct:.2f}%", li.item_no))
        elif li.total_to_date:
            notes.append(Note("zero_scheduled_value", "work billed against a $0 scheduled value", li.item_no))
    for item_no, n in seen.items():
        if n > 1:
            notes.append(Note("duplicate_item_no", f"item {item_no} appears {n} times", item_no))

    if totals is None:
        notes.append(Note("no_totals_row", "no grand total row found; row count cannot be cross-checked"))
        return notes
    for col in MONEY_COLUMNS:
        if col not in totals or (col == "retainage" and lines and lines[0].retainage is None):
            continue
        try:
            printed = parse_money(totals[col])
        except CellParseError:
            notes.append(Note("totals_unreadable", f"grand total for {col} unreadable: {totals[col]!r}"))
            continue
        summed = sum((getattr(li, col) for li in lines), Decimal("0"))
        if abs(summed - printed) > MONEY_TOLERANCE:
            notes.append(Note("totals_mismatch", f"{col}: lines sum to {summed}, printed total is {printed}"))
    return notes


def _looks_misaligned(lines: list[LineItem]) -> bool:
    """A sub can get a line wrong; a parser that shifted a column gets nearly all of them wrong."""
    if len(lines) < 3:
        return False
    bad = sum(
        1
        for li in lines
        if abs(li.previous_applications + li.this_period + li.stored_materials - li.total_to_date) > MONEY_TOLERANCE
        and abs(li.scheduled_value - li.total_to_date - li.balance_to_finish) > MONEY_TOLERANCE
    )
    return bad > len(lines) / 2


# --- Entry point ------------------------------------------------------------


def extract_pay_request(
    pdf_path: str | Path,
    *,
    sub_id: str,
    job_id: str,
    submitted_at: datetime,
    bank_account: str | None = None,
    request_id: str | None = None,
    allow_llm_fallback: bool = True,
    client=None,
) -> ExtractionResult:
    """Read one G703 PDF into a PayRequest.

    Identity fields (sub, job, bank account) come from the submission, not the
    document: a PDF can claim to be from anyone.
    """
    path = Path(pdf_path)
    pdf_bytes = path.read_bytes()
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    method, fallback_reason = "text_layer", None
    try:
        sheet = _read_text_layer(path)
        lines, parse_notes = _to_line_items(sheet)
        if _looks_misaligned(lines):
            raise _UseFallback("most lines fail D+E+F=G and C-G=H; columns likely misaligned")
    except (_UseFallback, CellParseError) as e:
        fallback_reason = str(e)
    except pdfplumber.utils.exceptions.PdfminerException as e:
        raise ExtractionError(f"could not open PDF: {e}") from e

    if fallback_reason is not None:
        if not allow_llm_fallback:
            raise ExtractionError(f"text layer unusable: {fallback_reason}")
        method = "claude"
        try:
            sheet = _read_with_claude(pdf_bytes, client)
            lines, parse_notes = _to_line_items(sheet)
            if _looks_misaligned(lines):
                raise ExtractionError("transcription fails D+E+F=G and C-G=H on most lines")
        except CellParseError as e:
            raise ExtractionError(f"transcription unusable: {e}") from e
        except (llm.PerceptionLLMError, anthropic.APIError) as e:
            raise ExtractionError(f"Claude transcription failed: {e}") from e

    if not lines:
        raise ExtractionError("no line items found")
    if not sheet.period_to:
        raise ExtractionError("sheet has no 'period to' date")
    try:
        period_end = parse_date(sheet.period_to)
    except CellParseError as e:
        raise ExtractionError(str(e)) from e

    notes = sheet.notes + parse_notes + check_consistency(lines, sheet.totals)
    pay_request = PayRequest(
        id=request_id or f"pr_{uuid.uuid4().hex[:12]}",
        sub_id=sub_id,
        job_id=job_id,
        period_end=period_end,
        lines=lines,
        g702=None,
        bank_account=bank_account,
        submitted_at=submitted_at,
        source_doc=str(path),
    )
    return ExtractionResult(pay_request, method, notes, sha256, fallback_reason)
