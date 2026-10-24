"""Lien release (conditional / unconditional waiver, progress / final) PDF -> Release.

Text layer first, Claude transcription for scans, same as the G703. The form's title
decides its kind; the body's own language is cross-checked against it, because a
release titled "conditional" that says the claimant "has been paid" (or the reverse)
is exactly the paper a bad actor would send.
"""

import re
import uuid
from datetime import date
from pathlib import Path

import anthropic
import pdfplumber

from perception import llm
from perception.common import (
    DATE_TOKEN, DocResult, ExtractionError, Note, UseFallback, labelled, sha256_of, visible_lines, visible_page,
)
from perception.numbers import CellParseError, parse_date, parse_money
from shared.schema import Release, ReleaseKind


class NotARelease(ExtractionError):
    pass


_TITLE = re.compile(r"\b(UN)?CONDITIONAL\s+WAIVER\s+AND\s+RELEASE\s+(?:ON|UPON)\s+(PROGRESS|FINAL)\s+PAYMENT\b", re.I)
_CONDITIONAL_TEXT = re.compile(r"effective\s+only\s+(?:on|upon)\s+the\s+claimant'?s\s+receipt\s+of\s+payment", re.I)
_PAID_TEXT = re.compile(r"\bhas\s+been\s+paid\b", re.I)
_SIGNATURE_LABEL = r"CLAIMANT'?S\s+SIGNATURE"
_AMOUNT_LABEL = r"(?:AMOUNT\s+OF\s+(?:PAYMENT|CHECK)|PAYMENT\s+AMOUNT)"


def _kind(title_text: str) -> ReleaseKind:
    m = _TITLE.search(title_text)
    unconditional, stage = bool(m.group(1)), m.group(2).lower()
    return ReleaseKind(f"{'unconditional' if unconditional else 'conditional'}_{stage}")


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _signature_image_near_label(path: Path) -> bool:
    """A handwritten signature is usually an image beside the signature label."""
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            page = visible_page(page, page_no, [])
            if page is None:
                continue
            for hit in page.search(_SIGNATURE_LABEL, regex=True, case=False):
                if any(abs(float(im["top"]) - float(hit["top"])) < 40 and float(im["x0"]) >= float(hit["x0"])
                       for im in page.images):
                    return True
    return False


def _read_text_layer(path: Path, notes: list[Note]) -> dict:
    lines = visible_lines(path, notes)
    text = "\n".join(line for _, line in lines)
    titles = {m.group(0).upper() for m in _TITLE.finditer(text)}
    if not titles:
        raise UseFallback("no lien release title found in the text layer")
    if len({_kind(t) for t in titles}) > 1:
        raise ExtractionError(f"document carries more than one release title: {sorted(titles)}")
    signature = labelled(lines, _SIGNATURE_LABEL)
    typed = [v for v in signature if v.strip("_ .")]
    return {
        "title": next(iter(titles)),
        "through_date": labelled(lines, r"THROUGH\s+DATE", DATE_TOKEN),
        "amount": labelled(lines, _AMOUNT_LABEL, r"(\(?-?\$?\s*[\d,]+(?:\.\d+)?\)?)"),
        "claimant": labelled(lines, r"NAME\s+OF\s+CLAIMANT"),
        "signed": bool(typed) or _signature_image_near_label(path),
        "signature_is_image": not typed,
        "conditional_language": bool(_CONDITIONAL_TEXT.search(text)),
        "paid_language": bool(_PAID_TEXT.search(text)),
    }


def _one(values: list[str], what: str) -> str:
    if not values:
        raise UseFallback(f"no {what} found in the text layer")
    if len(values) > 1:
        raise ExtractionError(f"release shows more than one {what}: {values}")
    return values[0]


def extract_release(
    pdf_path: str | Path,
    *,
    sub_id: str,
    job_id: str,
    received_at: date,
    release_id: str | None = None,
    expected_claimant: str | None = None,
    allow_llm_fallback: bool = True,
    client=None,
) -> DocResult:
    """Read one lien release. Sub, job and the date it arrived come from the submission."""
    path = Path(pdf_path)
    data = path.read_bytes()
    sha = sha256_of(data)
    notes: list[Note] = []
    method, fallback_reason = "text_layer", None
    try:
        raw = _read_text_layer(path, notes)
        title = raw["title"]
        through, amount = _one(raw["through_date"], "through date"), _one(raw["amount"], "payment amount")
        claimant = raw["claimant"][0] if raw["claimant"] else ""
        signed = raw["signed"]
        if signed and raw["signature_is_image"]:
            notes.append(Note("signature_is_image", "signed with an image; a person may want to look at it"))
    except UseFallback as e:
        fallback_reason = str(e)
        if not allow_llm_fallback:
            raise ExtractionError(f"text layer unusable: {e}") from e
        method = "claude"
        try:
            t = llm.transcribe_release(data, client)
        except (llm.PerceptionLLMError, anthropic.AnthropicError) as e2:
            raise ExtractionError(f"Claude transcription failed: {e2}") from e2
        if not t.is_lien_release or not _TITLE.search(t.title):
            raise NotARelease(f"not a recognized lien release form (title: {t.title!r})")
        if t.unreadable_fields:
            raise ExtractionError(f"fields could not be read with certainty: {', '.join(t.unreadable_fields)}")
        title, through, amount, claimant, signed = t.title, t.through_date, t.amount, t.claimant, t.signature_present
        raw = {"conditional_language": None, "paid_language": None}

    kind = _kind(title)
    try:
        through_date, amount_value = parse_date(through), parse_money(amount, blank_is_zero=False)
    except CellParseError as e:
        raise ExtractionError(f"release field unreadable: {e}") from e

    conditional = kind in (ReleaseKind.conditional_progress, ReleaseKind.conditional_final)
    if raw["conditional_language"] is not None:
        if conditional and not raw["conditional_language"]:
            notes.append(Note("title_body_mismatch", "titled conditional, but the body doesn't make it conditional on payment"))
        if not conditional and raw["conditional_language"]:
            notes.append(Note("title_body_mismatch", "titled unconditional, but the body makes it conditional on payment"))
    if expected_claimant and claimant and _norm_name(claimant) != _norm_name(expected_claimant):
        notes.append(Note("claimant_mismatch", f"release names {claimant!r}, expected {expected_claimant!r}"))
    if not claimant:
        notes.append(Note("no_claimant", "release does not name its claimant"))

    release = Release(
        id=release_id or f"rel_{sha[:12]}",
        sub_id=sub_id,
        job_id=job_id,
        kind=kind,
        through_date=through_date,
        amount=amount_value,
        signed=signed,
        received_at=received_at,
    )
    return DocResult(release, method, notes, sha, fallback_reason)
