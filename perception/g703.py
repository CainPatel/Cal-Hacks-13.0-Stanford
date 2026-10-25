"""G703 continuation sheet -> PayRequest.

Two paths produce the same intermediate RawSheet of printed strings:
  1. the PDF's text layer, read with pdfplumber (exact, preferred)
  2. Claude transcription (only when the text layer is missing or unusable)
One parser turns RawSheet into LineItems, and one set of checks reports
inconsistencies. Checks never change values: a sub's arithmetic errors are
evidence for the kernel, so they are reported and passed through.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import anthropic
import pdfplumber
import pdfplumber.utils.exceptions

from perception import llm
from perception.common import ExtractionError, Note, UseFallback as _UseFallback, text_size, tolerance, visible_page
from perception.numbers import CellParseError, parse_date, parse_money, parse_pct
from shared.schema import G702, LineItem, PayRequest

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


class NotAG703(ExtractionError):
    pass


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
    remit_account: str
    has_retainage_column: bool
    rows: list[RawRow]
    totals: dict[str, str] | None
    g702: dict[str, str] | None  # G702 field -> printed amount
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


_DATE_TOKEN = r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})"
_HEADER_PATTERNS = {
    # "APPLICATION NO: 6", also "APPLICATION NO: Draw #1"
    "application_no": re.compile(r"APPLICATION\s+NO\.?\s*:?\s*(?:DRAW\s*)?#?\s*([A-Z0-9\-]*\d[A-Z0-9\-]*)", re.I),
    "application_date": re.compile(r"APPLICATION\s+DATE\s*:?\s*" + _DATE_TOKEN, re.I),
    "period_to": re.compile(r"PERIOD\s+TO\s*:?\s*" + _DATE_TOKEN, re.I),
    "remit_account": re.compile(
        r"REMIT[^\n:]{0,40}?ACCOUNT\s*(?:NO\.?|NUMBER)?\s*:?\s*([A-Za-z0-9][A-Za-z0-9:\-]{3,})", re.I),
}
# Fields that decide where money goes or which period is billed: pages must agree.
_MUST_AGREE = {"period_to": "billing period", "remit_account": "remit-to account", "application_no": "application number"}

# G702 summary lines, by their printed label. The amount is the first one after the
# label (after an optional "(Line 1 + 2)"-style note, see _g702_amount), so a blank amount reads as 0
# exactly like a blank G703 cell. Patterns are tried in order; the first label found wins.
_G702_LABELS: dict[str, list[re.Pattern]] = {
    field_: [re.compile(p, re.I) for p in pats]
    for field_, pats in {
        "original_contract_sum": [r"\b1\.\s*ORIGINAL\s+CONTRACT\s+SUM\b"],
        "net_change_orders": [r"\b2\.\s*NET\s+CHANGE\s+BY\s+CHANGE\s+ORDERS\b"],
        "contract_sum_to_date": [r"\b3\.\s*CONTRACT\s+SUM\s+TO\s+DATE\b"],
        "total_completed_stored": [r"\b4\.\s*TOTAL\s+COMPLETED\s*(?:&|AND)\s*STORED\s+TO(?:\s+DATE)?\b"],
        # The AIA form puts the amount on a "Total Retainage" sub-line under "5. RETAINAGE:".
        "retainage": [r"\bTOTAL\s+RETAINAGE\b", r"\b5\.\s*RETAINAGE\b"],
        "total_earned_less_retainage": [r"\b6\.\s*TOTAL\s+EARNED\s+LESS\s+RETAINAGE\b"],
        "less_previous_certificates": [r"\b7\.\s*LESS\s+PREVIOUS\s+CERTIFICATES(?:\s+FOR\s+PAYMENT)?\b"],
        "current_payment_due": [r"\b8\.\s*CURRENT\s+PAYMENT\s+DUE\b"],
    }.items()
}
_LEADER = re.compile(r"^\s*(?:\([^)]*\)\s*)?[:.\s]*")


_AIA_G702_TITLE = re.compile(r"APPLICATION\s+AND\s+CERTIFICAT(?:E|ION)\s+FOR\s+PAYMENT", re.I)


def _text(page) -> str:
    tol = tolerance(page)
    return page.extract_text(x_tolerance=tol, y_tolerance=tol) or ""


def _g702_lines(page, text: str) -> list[str]:
    """Lines to search for G702 labels. The AIA form prints the architect's certificate on the
    same baselines as lines 1-9, so on that page only the left column is read; otherwise
    "1. ORIGINAL CONTRACT SUM $ 435,000.00 ARCHITECT'S CERTIFICATE..." would be one line."""
    if _AIA_G702_TITLE.search(text):
        left = page.crop((0, 0, float(page.width) / 2, float(page.height)))
        return _text(left).splitlines()
    return text.splitlines()


def read_g702(path: str | Path) -> tuple[dict[str, str], dict[str, str], list[Note]]:
    """A G702 sent as its own PDF: printed amounts per field, header values, notes."""
    notes: list[Note] = []
    lines: list[str] = []
    header: dict[str, set[str]] = {k: set() for k in _HEADER_PATTERNS}
    try:
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                page = visible_page(page, page_no, notes)
                if page is None:
                    continue
                text = _text(page)
                lines += _g702_lines(page, text)
                for key, pattern in _HEADER_PATTERNS.items():
                    for m in pattern.finditer(text):
                        header[key].add(m.group(1).strip())
    except pdfplumber.utils.exceptions.PdfminerException as e:
        raise ExtractionError(f"could not open G702 PDF: {e}") from e
    except _UseFallback as e:
        raise ExtractionError(f"G702 is not readable as text ({e}); send it with the G703 or as a digital PDF") from e
    fields, conflicts = _g702_from_text(lines)
    if conflicts:
        raise ExtractionError(f"G702 lines printed more than once with different amounts: {conflicts}")
    for key, values in header.items():
        if len(values) > 1:
            raise ExtractionError(f"G702 shows more than one {key}: {sorted(values)}")
    return fields, {k: next(iter(v)) for k, v in header.items() if v}, notes


_AMOUNT_TOKEN = re.compile(r"^(\(?-?\$?\(?[\d,]*\d(?:\.\d+)?\)?|-)$")
_DOLLAR_AMOUNT = re.compile(r"\$\s*\$?\s*(\(?-?[\d,]*\d(?:\.\d+)?\)?)")
_NEXT_ITEM = re.compile(r"^\s*(\d{1,2}|[a-z])\.\s*\S")  # "6. TOTAL EARNED...", "a. % of Completed Work"
_LOOKAHEAD = 3


def _g702_amount(lines: list[str], i: int, rest: str) -> str:
    """The amount printed for a label found on lines[i], `rest` being the text after it. Only the
    first amount counts: whatever follows on the line ("By:", the architect's column) isn't part of
    it. A label that wraps ("7. LESS PREVIOUS CERTIFICATES FOR" / "PAYMENT (Line 6 ...) $ 0.00")
    prints its amount on a following line, before the next numbered or lettered line starts."""
    rest = _LEADER.sub("", rest, count=1).strip().lstrip("$ ")
    token = rest.split()[0] if rest else ""
    if _AMOUNT_TOKEN.match(token):
        return token
    for line in lines[i + 1:i + 1 + _LOOKAHEAD]:
        if _NEXT_ITEM.match(line):
            break
        m = _DOLLAR_AMOUNT.search(line)
        if m:
            return m.group(1)
    return ""  # printed blank: reads as 0, like a blank G703 cell


def _g702_from_text(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """Printed amount per G702 field, and the fields whose label appears with conflicting amounts."""
    found: dict[str, set[str]] = {}
    for field_, patterns in _G702_LABELS.items():
        for pattern in patterns:
            hits = [(i, m) for i, line in enumerate(lines) for m in [pattern.search(line)] if m]
            if hits:
                found[field_] = {_g702_amount(lines, i, lines[i][m.end():]) for i, m in hits}
                break
    conflicts = [f for f, v in found.items() if len(v) > 1]
    return {f: next(iter(v)) for f, v in found.items() if len(v) == 1}, conflicts
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
    text_lines: list[str] = []
    seen: list[Note] = []  # what visible_page found; kept if the sheet is read again by position

    with pdfplumber.open(path) as pdf:
        pages = []
        for page_no, page in enumerate(pdf.pages, start=1):
            page = visible_page(page, page_no, seen)
            if page is None:
                continue  # blank page
            pages.append((page_no, page))

            text = _text(page)
            text_lines.extend(_g702_lines(page, text))
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

        if st.mapping is None or not st.rows:
            # No ruled table, or one around the header only (spreadsheet exports draw borders there)
            st = _SheetState()
            for page_no, page in pages:
                found = _position_rows(page)
                if found:
                    st.mapping, body, outside = found
                    _collect_rows(body, st, page_no)
                    if outside:
                        st.notes.append(Note("outside_sheet", f"page {page_no}: ignored {outside} words right of column I"))
            if st.mapping is not None:
                st.notes.append(Note("read_by_position", "line items read by position under the A-I column letters"))
    st.notes[:0] = seen
    if st.mapping is None:
        raise _UseFallback("no G703 column header found in the text layer")
    if not st.rows:
        raise _UseFallback("header found but no line items parsed")
    if "stored_materials" not in st.mapping.values():
        st.notes.append(Note("no_stored_column", "sheet has no stored-materials column; read as $0"))

    resolved = {}
    for key, values in header_values.items():
        if len(values) > 1:
            if key in _MUST_AGREE:
                # These decide which period is billed and where money goes; never pick one.
                raise ExtractionError(f"pages disagree on the {_MUST_AGREE[key]}: {sorted(values)}")
            st.notes.append(Note("header_mismatch", f"{key} differs between pages: {sorted(values)}"))
        resolved[key] = next(iter(values)) if len(values) == 1 else ""

    g702, conflicts = _g702_from_text(text_lines)
    if conflicts:
        raise ExtractionError(f"G702 lines printed more than once with different amounts: {conflicts}")

    return RawSheet(
        application_no=resolved["application_no"],
        application_date=resolved["application_date"],
        period_to=resolved["period_to"],
        remit_account=resolved["remit_account"],
        has_retainage_column="retainage" in st.mapping.values(),
        rows=st.rows,
        totals=st.totals,
        g702=g702 or None,
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
        no_values = all(v == "" for v in values.values()) or _no_money(values)
        label = f"{item} {desc}"

        if not item and not desc and no_values:
            continue
        if _is_letter_row(list(cells.values())) or (_find_header([list(cells.values())]) is not None):
            continue  # repeated header or the A..I column-letter row
        if not no_values and _TOTAL_LABEL.search(label) and (
                not item or _TOTAL_LABEL.search(item) or _numbered_grand_total(desc, values, st.rows)):
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


def _numbered_grand_total(desc: str, values: dict[str, str], rows: list[RawRow]) -> bool:
    """A grand total printed with an item number ("181  Total Project"): only when it says so and its
    scheduled value is the sum of the lines above it, so a line item named "Total station" stays a line."""
    if not re.match(r"\s*(GRAND\s+)?TOTALS?\b", desc, re.I):
        return False
    try:
        return abs(parse_money(values.get("scheduled_value"), blank_is_zero=False) -
                   sum(parse_money(r.cells.get("scheduled_value")) for r in rows)) <= MONEY_TOLERANCE
    except CellParseError:
        return False


def _no_money(values: dict[str, str]) -> bool:
    """True when every amount on a row is blank or zero: a heading or placeholder ("00 - Contract
    Requirements  $0.00 ...") that bills nothing and can't be paid, so it isn't a line item."""
    try:
        return all(parse_money(v) == 0 if k != "pct_complete" else parse_pct(v)[0] == 0
                   for k, v in values.items() if v.strip())
    except CellParseError:
        return False


# Reading by position, for sheets whose line items aren't in a ruled table (spreadsheet exports):
# the A..I letter row every AIA G703 prints gives each column's center.
_LETTER_KEYS = dict(zip("ABCDEFGHI", ("item_no", "description", "scheduled_value", "previous_applications",
                                      "this_period", "stored_materials", "total_to_date", "balance_to_finish",
                                      "retainage")))
_AMOUNT = re.compile(r"^\(?-?\$?\s?\(?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?\)?%?$")


def _is_amount(text: str) -> bool:
    t = text.strip()
    return bool(re.search(r"\d", t)) and bool(_AMOUNT.match(t)) and bool(re.search(r"[$,.%]", t)) or t in ("-", "$-")


def _lines(words: list[dict], tol: float = 3) -> list[list[dict]]:
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) <= tol:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in out]


def _position_rows(page) -> tuple[dict[int, str], list[list[str]], int] | None:
    """(column index -> key, rows of cells) read by word position under the A..I letter row, or None.
    Amounts right of column I (a spreadsheet's working columns printed alongside) are not the sheet's."""
    tol = tolerance(page)
    # Glyphs closer than a fraction of a space are one word: an export can print a right-aligned
    # item number and the description after it less than a space apart ("2" "007316.13 - ...").
    lines = _lines(page.extract_words(x_tolerance=0.15 * text_size(page), y_tolerance=tol), tol)
    at = next((i for i, line in enumerate(lines) if [w["text"] for w in line] == list("ABCDEFGHI")), None)
    if at is None:
        return None
    center = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in lines[at]}
    # % (G / C) sits between G and H and has no letter: its header word, else halfway
    pct = next(((w["x0"] + w["x1"]) / 2 for line in lines[at + 1:at + 6] for w in line
                if w["text"] in ("%", "(G") and center["G"] < (w["x0"] + w["x1"]) / 2 < center["H"]),
               (center["G"] + center["H"]) / 2)
    columns = [(_LETTER_KEYS[c], center[c]) for c in "CDEFG"] + [("pct_complete", pct)] + \
              [(_LETTER_KEYS[c], center[c]) for c in "HI"]
    # the item column ends where its header words ("ITEM", "NO.") end
    a_words = [w for line in lines[at + 1:at + 6] for w in line if abs((w["x0"] + w["x1"]) / 2 - center["A"])
               < abs((w["x0"] + w["x1"]) / 2 - center["B"]) and not _is_amount(w["text"])]
    item_right = max((w["x1"] for w in a_words), default=center["A"] + (center["B"] - center["A"]) / 4) + tol
    amounts_from = (center["B"] + center["C"]) / 2
    sheet_right = center["I"] + (center["I"] - center["H"]) / 2
    keys = ["item_no", "description"] + [k for k, _ in columns]
    rows, started, outside = [], False, 0
    for line in lines[at + 1:]:
        cells = {k: [] for k in keys}
        for i, w in enumerate(line):
            mid = (w["x0"] + w["x1"]) / 2
            if mid > sheet_right:
                outside += 1
                continue
            if i == 0 and w["x1"] <= item_right:
                cells["item_no"].append(w["text"])
            elif mid >= amounts_from and _is_amount(w["text"]):
                cells[min(columns, key=lambda c: abs(c[1] - mid))[0]].append(w["text"])
            else:
                cells["description"].append(w["text"])
        if not started:  # the header block ends at the first line that starts with an item number
            item = " ".join(cells["item_no"])
            if not item or item.upper() in ("ITEM", "NO.", "NO") or not _ITEM_NO.match(item):
                continue
            started = True
        rows.append([" ".join(cells[k]) for k in keys])
    return dict(enumerate(keys)), rows, outside


# --- Claude transcription ---------------------------------------------------


def _read_with_claude(pdf_bytes: bytes, client) -> RawSheet:
    t = llm.transcribe_g703(pdf_bytes, client)
    if not t.is_g703:
        raise NotAG703("document is not a G703 continuation sheet")
    if t.unreadable_cells:
        # A guessed digit is worse than no answer: someone has to look at the paper.
        raise ExtractionError(f"cells could not be read with certainty: {', '.join(t.unreadable_cells)}")
    # A row with no money on it (blank or $0.00 everywhere) is a section heading ("HVAC", "Division 23")
    # or a placeholder: it can't be paid, so it is noted and left out, as on the text-layer path.
    amounts = (*MONEY_COLUMNS, "pct_complete")
    headings = [r for r in t.rows if _no_money({k: getattr(r, k) for k in amounts})]
    t.rows = [r for r in t.rows if r not in headings]
    notes = [Note("section_heading", f"skipped heading {r.item_no.strip()} {r.description.strip()!r}",
                  r.item_no.strip() or None) for r in headings]
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
        remit_account=t.remit_account.strip(),
        has_retainage_column=t.has_retainage_column,
        rows=[RawRow(r.item_no.strip(), r.description.strip(), cells(r)) for r in t.rows],
        totals=cells(t.totals_row) if t.totals_row else None,
        g702=t.g702.model_dump() if t.g702 else None,
        notes=notes,
    )


# --- Shared parsing and checks ----------------------------------------------


def _to_g702(sheet: RawSheet) -> G702:
    if not sheet.g702:
        raise ExtractionError("no G702 application summary found")
    missing = [f for f in G702.model_fields if f not in sheet.g702]
    if missing:
        raise ExtractionError(f"G702 summary is missing: {', '.join(missing)}")
    try:
        return G702(**{f: parse_money(sheet.g702[f]) for f in G702.model_fields})
    except CellParseError as e:
        raise ExtractionError(f"G702 amount unreadable: {e}") from e


def _to_line_items(sheet: RawSheet) -> tuple[list[LineItem], list[Note]]:
    if not sheet.has_retainage_column:
        # The schema needs per-line retainage; a guessed 0 would read as a retainage error.
        raise ExtractionError("sheet has no retainage column")
    notes: list[Note] = []
    pct_raw = [(r, *parse_pct(r.cells.get("pct_complete"))) for r in sheet.rows]
    # Some sheets print 0.60 rather than 60%. With no % sign anywhere, pick the
    # reading that matches G/C. No "every value <= 1" gate: a line billed past its
    # scheduled value prints as 1.12, and misreading it as 1.12% would hide the over-bill.
    any_sign = any(sign for _, _, sign in pct_raw)
    fractional = False
    if not any_sign and any(v > 0 for _, v, _ in pct_raw):
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
            money = {k: parse_money(r.cells.get(k), blank_is_zero=(k != "scheduled_value")) for k in MONEY_COLUMNS}
        except CellParseError as e:
            raise CellParseError(f"item {r.item_no}: {e}") from e
        lines.append(
            LineItem(
                item_no=r.item_no,
                description=r.description,
                pct_complete=pct * 100 if fractional else pct,
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
        if col not in totals:
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


def check_g702(g: G702, lines: list[LineItem]) -> list[Note]:
    """Report where the G702 summary disagrees with itself or with the G703 lines."""
    notes = []
    pairs = [
        ("g702_line3", g.original_contract_sum + g.net_change_orders, g.contract_sum_to_date, "line 1 + line 2"),
        ("g702_line6", g.total_completed_stored - g.retainage, g.total_earned_less_retainage, "line 4 - line 5"),
        ("g702_line8", g.total_earned_less_retainage - g.less_previous_certificates, g.current_payment_due,
         "line 6 - line 7"),
        ("g702_vs_g703_completed", sum((l.total_to_date for l in lines), Decimal("0")), g.total_completed_stored,
         "G703 total completed (column G)"),
        ("g702_vs_g703_retainage", sum((l.retainage for l in lines), Decimal("0")), g.retainage,
         "G703 total retainage (column I)"),
    ]
    for code, expected, printed, what in pairs:
        if abs(expected - printed) > MONEY_TOLERANCE:
            notes.append(Note(code, f"G702 shows {printed} but {what} is {expected}"))
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


def _application_number(text: str | None) -> int | None:
    """The application number as printed ("6", "#01", "Draw #1"), or None if it isn't one."""
    m = re.fullmatch(r"(?:(?:DRAW|PAY\s*APP(?:LICATION)?|APPLICATION|APP|NO\.?)\s*)?#?\s*(\d{1,4})",
                     (text or "").strip(), re.I)
    return int(m.group(1)) if m else None


def extract_pay_request(
    pdf_path: str | Path,
    *,
    sub_id: str,
    job_id: str,
    submitted_at: datetime,
    request_id: str | None = None,
    allow_llm_fallback: bool = True,
    client=None,
    g702_pdf: str | Path | None = None,
) -> ExtractionResult:
    """Read one pay application PDF (G703 continuation sheet + G702 summary) into a PayRequest.

    Sub and job come from the submission, not the document: a PDF can claim to
    be from anyone. The remit-to account is read from the document because it
    is the sub's claim that rule 10 checks against the account on file.

    g702_pdf is the G702 when the sub sends it as a separate file. If both
    documents carry a G702, application number or period, they must agree.
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
    if g702_pdf is not None:
        fields, header, g_notes = read_g702(g702_pdf)
        sheet.notes.extend(g_notes)
        try:
            for key in ("application_no", "period_to"):
                theirs, ours = header.get(key), getattr(sheet, key)
                same = theirs == ours or bool(theirs and ours and (
                    parse_date(theirs) == parse_date(ours) if key == "period_to"
                    else _application_number(theirs) == _application_number(ours) is not None))
                if theirs and ours and not same:
                    raise ExtractionError(f"G702 and G703 disagree on {key}: {theirs!r} vs {ours!r}")
                if theirs and not ours:
                    setattr(sheet, key, theirs)
            if sheet.g702 and fields and {k: parse_money(v) for k, v in sheet.g702.items()} != {
                    k: parse_money(v) for k, v in fields.items()}:
                raise ExtractionError("the G702 file and the G702 inside the pay application disagree")
        except CellParseError as e:
            raise ExtractionError(f"G702 value unreadable: {e}") from e
        sheet.g702 = sheet.g702 or fields or None
    # Everything the kernel needs besides the lines, said at once, so the sub can fix it in one go.
    number = _application_number(sheet.application_no)
    missing = []
    if not sheet.period_to:
        missing.append("sheet has no 'period to' date")
    if number is None:
        missing.append(f"application number missing or not a number: {sheet.application_no!r}")
    if not sheet.remit_account:
        missing.append("no remit-to bank account on the pay application")
    if not sheet.g702:
        missing.append("no G702 application summary found (send the G702 page with it)")
    if missing:
        raise ExtractionError(f"read {len(lines)} line items, but: " + "; ".join(missing))
    try:
        period_end = parse_date(sheet.period_to)
    except CellParseError as e:
        raise ExtractionError(str(e)) from e
    sheet.application_no = str(number)
    g702 = _to_g702(sheet)

    notes = sheet.notes + parse_notes + check_consistency(lines, sheet.totals) + check_g702(g702, lines)
    pay_request = PayRequest(
        id=request_id or f"pr_{uuid.uuid4().hex[:12]}",
        number=int(sheet.application_no),
        sub_id=sub_id,
        job_id=job_id,
        period_end=period_end,
        lines=lines,
        g702=g702,
        bank_account=sheet.remit_account,
        submitted_at=submitted_at,
        source_doc=str(path),
    )
    return ExtractionResult(pay_request, method, notes, sha256, fallback_reason)
