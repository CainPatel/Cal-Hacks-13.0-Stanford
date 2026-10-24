"""Certificate of insurance PDF -> COI (general liability and workers' comp expiry, additional insured).

Text layer first, Claude transcription for scans or layouts the text can't settle. A
policy row's expiry is the latest date printed on it (ACORD rows print effective, then
expiration). Additional-insured status is read only from an explicit statement; anything
less settles nothing and goes to the fallback rather than defaulting either way.
"""

import re
from pathlib import Path

import anthropic

from perception import llm
from perception.common import DATE_RE, DocResult, ExtractionError, Note, UseFallback, labelled, sha256_of, visible_lines
from perception.numbers import CellParseError, parse_date
from shared.schema import COI


class NotACOI(ExtractionError):
    pass


_GL_ROW = re.compile(r"\bGENERAL\s+LIABILITY\b", re.I)
_WC_ROW = re.compile(r"\bWORKERS'?\s*COMP", re.I)
_AI_ANSWER = re.compile(r"ADDITIONAL\s+INSURED\s*:?\s*(YES|NO|Y|N)\b", re.I)
_AI_NEGATED = re.compile(r"\b(?:IS|ARE)\s+NOT\s+(?:NAMED\s+)?(?:AS\s+)?(?:AN\s+)?ADDITIONAL\s+INSUREDS?\b", re.I)
_AI_STATED = re.compile(r"\b(?:IS|ARE)\s+(?:NAMED\s+)?(?:AS\s+)?(?:AN\s+)?ADDITIONAL\s+INSUREDS?\b", re.I)
_TITLE = re.compile(r"CERTIFICATE\s+OF\s+(?:LIABILITY\s+)?INSURANCE", re.I)


def _expiry(lines: list[tuple[int, str]], row: re.Pattern, what: str) -> str:
    """The latest date on the policy's row (or the next two lines, where ACORD wraps)."""
    found = set()
    for i, (_, line) in enumerate(lines):
        if row.search(line):
            for _, candidate in lines[i:i + 3]:
                dates = DATE_RE.findall(candidate)
                if dates:
                    found.add(max(dates, key=lambda d: parse_date(d)))
                    break
    if not found:
        raise UseFallback(f"no {what} expiration date found in the text layer")
    if len({parse_date(d) for d in found}) > 1:
        raise ExtractionError(f"certificate shows more than one {what} expiration: {sorted(found)}")
    return found.pop()


def _additional_insured(text: str) -> bool:
    answers = {m.group(1).upper()[0] for m in _AI_ANSWER.finditer(text)}
    negated = bool(_AI_NEGATED.search(text))
    stated = bool(_AI_STATED.search(_AI_NEGATED.sub("", text)))
    says_yes, says_no = "Y" in answers or stated, "N" in answers or negated
    if says_yes and says_no:
        raise ExtractionError("certificate both grants and denies additional-insured status")
    if not (says_yes or says_no):
        raise UseFallback("additional-insured status not stated in the text layer")
    return says_yes


def extract_coi(
    pdf_path: str | Path,
    *,
    sub_id: str,
    coi_id: str | None = None,
    expected_insured: str | None = None,
    allow_llm_fallback: bool = True,
    client=None,
) -> DocResult:
    """Read one certificate of insurance. Which sub it covers comes from the submission."""
    path = Path(pdf_path)
    data = path.read_bytes()
    sha = sha256_of(data)
    notes: list[Note] = []
    method, fallback_reason = "text_layer", None
    try:
        lines = visible_lines(path, notes)
        text = "\n".join(line for _, line in lines)
        if not _TITLE.search(text):
            raise UseFallback("no certificate-of-insurance title in the text layer")
        gl, wc = _expiry(lines, _GL_ROW, "general liability"), _expiry(lines, _WC_ROW, "workers' comp")
        additional = _additional_insured(text)
        insured = next(iter(labelled(lines, r"^\s*INSURED")), "")
    except UseFallback as e:
        fallback_reason = str(e)
        if not allow_llm_fallback:
            raise ExtractionError(f"text layer unusable: {e}") from e
        method = "claude"
        try:
            t = llm.transcribe_coi(data, client)
        except (llm.PerceptionLLMError, anthropic.AnthropicError) as e2:
            raise ExtractionError(f"Claude transcription failed: {e2}") from e2
        if not t.is_certificate_of_insurance:
            raise NotACOI("document is not a certificate of insurance")
        if t.unreadable_fields:
            raise ExtractionError(f"fields could not be read with certainty: {', '.join(t.unreadable_fields)}")
        if t.additional_insured.strip().lower() not in ("yes", "no"):
            raise ExtractionError("additional-insured status is unclear on the certificate")
        gl, wc, additional, insured = (t.general_liability_expiry, t.workers_comp_expiry,
                                       t.additional_insured.strip().lower() == "yes", t.insured)

    try:
        gl_expiry, wc_expiry = parse_date(gl), parse_date(wc)
    except CellParseError as e:
        raise ExtractionError(f"certificate date unreadable: {e}") from e
    if expected_insured and insured and re.sub(r"[^a-z0-9]", "", insured.lower()) != re.sub(
            r"[^a-z0-9]", "", expected_insured.lower()):
        notes.append(Note("insured_mismatch", f"certificate insures {insured!r}, expected {expected_insured!r}"))

    coi = COI(id=coi_id or f"coi_{sub_id}_{sha[:8]}", sub_id=sub_id, gl_expiry=gl_expiry, wc_expiry=wc_expiry,
              additional_insured=additional)
    return DocResult(coi, method, notes, sha, fallback_reason)
