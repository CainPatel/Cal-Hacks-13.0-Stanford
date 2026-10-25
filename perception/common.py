"""What every perception extractor shares: notes, errors, results, and reading only the
text a person can actually see on a PDF page.
"""

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pdfplumber
import pdfplumber.utils.exceptions


class ExtractionError(RuntimeError):
    """The document could not be turned into a record by any path."""


class UseFallback(Exception):
    """The text layer can't be trusted for this document; try Claude transcription."""


@dataclass
class Note:
    code: str
    message: str
    item_no: str | None = None


@dataclass
class DocResult:
    """One extracted record plus how it was read. `record` is a shared/schema.py model."""
    record: object
    method: str  # "text_layer", "claude", or "parsed" (email)
    notes: list[Note] = field(default_factory=list)
    sha256: str = ""
    fallback_reason: str | None = None


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_INK_RESOLUTION = 144
_INK_CONTRAST = 60  # grey levels between darkest and lightest pixel inside a glyph's box
_MAX_HIDDEN_SHARE = 0.2
_TINY_PT = 3  # text this small next to ordinary print is unreadable: treated as hidden
_SMALL_PRINT_PT = 7  # the smallest ordinary print the 144 dpi check is tuned for
_MAX_INK_RESOLUTION = 800


def text_size(page) -> float:
    """The page's ordinary text size in points (the median glyph). A wide spreadsheet printed
    shrunk to fit one page can be 2 pt throughout; a person reads it zoomed in."""
    sizes = sorted(c["size"] for c in page.chars if not c["text"].isspace())
    return sizes[len(sizes) // 2] if sizes else 10.0


def tolerance(page) -> float:
    """pdfplumber's x/y tolerance for joining glyphs into words and lines. Its 3 pt default assumes
    ordinary print; on a shrunk page it would join neighbouring rows and cells."""
    return min(3.0, text_size(page) / 2)


def _char_key(c: dict) -> tuple:
    return (c["text"], round(c["x0"], 2), round(c["top"], 2))


def _inked_char_keys(page) -> tuple[set[tuple], int]:
    """Keys of characters that actually show up when the page is rendered.

    A text layer can hold characters a person never sees: invisible render
    mode, white or near-white fill, zero alpha, or glyphs covered by a shape.
    Rendering the page and looking for ink inside each glyph's box catches all
    of these at once, so the parser reads what the GC sees on screen.

    A page printed shrunk to fit is rendered finer, so its glyphs get as many pixels
    as ordinary print does, and "tiny" is judged against the page's own text size.
    """
    size = text_size(page)
    resolution = min(_MAX_INK_RESOLUTION, _INK_RESOLUTION * max(1.0, _SMALL_PRINT_PT / size))
    tiny = min(_TINY_PT, size / 2)
    img = np.asarray(page.to_image(resolution=resolution).original.convert("L"), dtype=np.int16)
    scale = resolution / 72
    keep, hidden = set(), 0
    for c in page.chars:
        key = _char_key(c)
        if c["text"].isspace():
            keep.add(key)
            continue
        # Trim the box edges so ink bleeding in from a neighbouring glyph doesn't count.
        # An underscore's ink sits on the bottom edge of its box, so it keeps its full height.
        dx = (c["x1"] - c["x0"]) * 0.15
        dy = 0 if c["text"] == "_" else (c["bottom"] - c["top"]) * 0.1
        x0, x1 = int((c["x0"] + dx) * scale), math.ceil((c["x1"] - dx) * scale)
        y0, y1 = int((c["top"] + dy) * scale), math.ceil((c["bottom"] - dy) * scale)
        crop = img[max(y0, 0):y1, max(x0, 0):x1]
        if crop.size and int(crop.max()) - int(crop.min()) >= _INK_CONTRAST and c.get("size", 12) >= tiny:
            keep.add(key)
        else:
            hidden += 1
    return keep, hidden



def visible_page(page, page_no: int, notes: list[Note]):
    """The page with invisible characters removed, or None if it is blank.

    Raises UseFallback for a scan (no text layer), an OCR layer over a page-sized image,
    or a text layer that mostly doesn't match what's rendered.
    """
    raw_chars = len(page.chars)
    area = float(page.width * page.height)
    image_area = sum(float(im["width"] * im["height"]) for im in page.images)
    if raw_chars == 0:
        if page.images:
            raise UseFallback(f"page {page_no} is a scanned image with no text layer")
        return None
    if image_area > 0.5 * area:
        # Text over a page-sized image is usually an OCR layer, which can misread digits.
        raise UseFallback(f"page {page_no} looks like a scan with an OCR text layer")
    keep, hidden = _inked_char_keys(page)
    if hidden:
        printable = sum(1 for c in page.chars if not c["text"].isspace())
        if hidden > _MAX_HIDDEN_SHARE * printable:
            raise UseFallback(f"page {page_no}: text layer does not match the rendered page")
        notes.append(Note("hidden_text", f"page {page_no}: ignored {hidden} characters that are not visible on the page"))
        page = page.filter(lambda o: o.get("object_type") != "char" or _char_key(o) in keep)
    return page


def visible_lines(path: Path, notes: list[Note]) -> list[tuple[int, str]]:
    """(page number, line) for every visible text line in the PDF."""
    out = []
    try:
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                page = visible_page(page, page_no, notes)
                if page is not None:
                    out += [(page_no, line) for line in (page.extract_text() or "").splitlines()]
    except pdfplumber.utils.exceptions.PdfminerException as e:
        raise ExtractionError(f"could not open PDF: {e}") from e
    if not out:
        raise UseFallback("no visible text in the document")
    return out


DATE_TOKEN = r"(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})"
DATE_RE = re.compile(DATE_TOKEN)


def labelled(lines: list[tuple[int, str]], label: str, value: str = r"(.+?)") -> list[str]:
    """Every distinct value printed after `label` (a regex), case-insensitive."""
    pattern = re.compile(label + r"\s*:?\s*" + value + r"\s*$", re.I)
    found = []
    for _, line in lines:
        m = pattern.search(line)
        if m and m.group(1).strip() not in found:
            found.append(m.group(1).strip())
    return found
