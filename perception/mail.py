"""Email (.eml) -> Email, with the claims rule 10/11 and judgment care about.

Headers are parsed by code. The sender's domain comes from the From address itself,
never its display name. Claims are found by code (an account-like number, change
wording, deadline wording); when Claude is available it can only add flags, never clear
them, so an email crafted to talk its way past the model still gets flagged by the code.
"""

import re
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import anthropic

from perception import llm
from perception.common import DocResult, ExtractionError, Note, sha256_of
from shared.schema import Email, EmailClaims

# Tried in order: routing:account, then a number labelled as an account. A bare digit run
# is not enough: invoice and job numbers look the same.
_ACCOUNT_PATTERNS = [
    re.compile(r"\b(\d{9}[:/\-]\d{4,17})\b"),
    re.compile(r"(?:account|acct)\.?\s*(?:number|no\.?|#)?\s*:?\s*(\d[\d\- ]{4,20}\d)", re.I),
]
_PAYMENT_WORDS = r"(?:bank|account|routing|remit(?:tance)?|payment\s+(?:details|info(?:rmation)?|instructions)|ach|wire)"
_CHANGE_WORDS = r"(?:new|updated?|chang(?:e|ed|ing)|switch(?:ed|ing)?|moved|different|alternate)"
_BANK_CHANGE = re.compile(rf"\b{_CHANGE_WORDS}\b[^.!?\n]{{0,80}}\b{_PAYMENT_WORDS}\b"
                          rf"|\b{_PAYMENT_WORDS}\b[^.!?\n]{{0,80}}\b{_CHANGE_WORDS}\b", re.I)
_URGENCY = re.compile(
    r"\b(?:urgent(?:ly)?|asap|immediately|right\s+away|as\s+soon\s+as\s+possible|time[- ]sensitive|by\s+today"
    r"|no\s+later\s+than|by\s+end\s+of\s+(?:day|week)|before\s+(?:monday|tuesday|wednesday|thursday|friday"
    r"|saturday|sunday|tomorrow|the\s+(?:next\s+)?payment\s+run|eod))\b",
    re.I,
)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def _html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return re.sub(r"[ \t]+", " ", " ".join(p.parts))


def _domain(addr: str) -> str:
    """As printed. Case is kept on purpose: a capital I posing as a lowercase l is the evidence."""
    return addr.rsplit("@", 1)[1].strip() if "@" in addr else ""


def code_claims(subject: str, body: str) -> EmailClaims:
    text = f"{subject}\n{body}"
    account = next((m.group(1).strip() for p in _ACCOUNT_PATTERNS for m in [p.search(body)] if m), None)
    bank_change = bool(_BANK_CHANGE.search(text))
    return EmailClaims(bank_change=bank_change or account is not None, new_account=account,
                       urgency=bool(_URGENCY.search(text)))


def extract_email(eml: str | Path | bytes, *, email_id: str | None = None, use_llm: bool = True, client=None) -> DocResult:
    data = eml if isinstance(eml, bytes) else Path(eml).read_bytes()
    sha = sha256_of(data)
    msg = BytesParser(policy=policy.default).parsebytes(data)
    notes: list[Note] = []

    froms = getaddresses(msg.get_all("From", []))
    if len(froms) != 1 or "@" not in froms[0][1]:
        raise ExtractionError(f"email needs exactly one sender address, got {msg.get_all('From', [])!r}")
    display, addr = froms[0]
    addr = addr.strip()
    domain = _domain(addr)
    if "@" in display and _domain(parseaddr(display)[1] or display).lower() != domain.lower():
        notes.append(Note("display_name_shows_other_address", f"display name {display!r} differs from sender {addr!r}"))
    reply_to = [a for _, a in getaddresses(msg.get_all("Reply-To", []))]
    if any(_domain(a).lower() != domain.lower() for a in reply_to):
        notes.append(Note("reply_to_differs", f"replies go to {reply_to}, not the sender's domain {domain}"))

    try:
        sent_at = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    except (TypeError, ValueError):
        sent_at = None
    if sent_at is None:
        raise ExtractionError("email has no readable Date header")
    if sent_at.tzinfo is not None:  # the log and every other record use naive times; store UTC
        sent_at = sent_at.astimezone(timezone.utc).replace(tzinfo=None)

    # Mail clients show the HTML part when there is one, so that is the body a person reads.
    # Claims are checked across both parts: an innocent plain part can't hide what the HTML says.
    html_part, plain_part = msg.get_body(preferencelist=("html",)), msg.get_body(preferencelist=("plain",))
    html = _html_to_text(html_part.get_content()).strip() if html_part is not None else ""
    plain = plain_part.get_content().strip() if plain_part is not None else ""
    body = html or plain
    if html and plain and re.sub(r"\W+", " ", html).strip().lower() != re.sub(r"\W+", " ", plain).strip().lower():
        notes.append(Note("html_differs_from_plain", "the HTML and plain-text versions of this email say different things"))
    subject = str(msg["Subject"] or "").strip()

    both = code_claims(subject, html), code_claims(subject, plain)
    claims = EmailClaims(bank_change=any(c.bank_change for c in both),
                         new_account=next((c.new_account for c in both if c.new_account), None),
                         urgency=any(c.urgency for c in both))
    method = "parsed"
    if use_llm:
        try:
            flags = llm.classify_email(subject, body, client)
            extra = {}
            if flags.asks_to_change_payment_details and not claims.bank_change:
                extra["bank_change"] = True
            if flags.pressures_for_speed and not claims.urgency:
                extra["urgency"] = True
            if extra:
                notes.append(Note("model_added_flags", f"Claude flagged {sorted(extra)} that the wording rules missed"))
                claims = claims.model_copy(update=extra)
            method = "parsed+claude"
        except (llm.PerceptionLLMError, anthropic.AnthropicError) as e:
            notes.append(Note("model_unavailable", f"claims from code only: {e}"))

    email = Email(id=email_id or f"email_{sha[:12]}", from_addr=addr, from_domain=domain, sent_at=sent_at,
                  subject=subject, body=body, claims=claims)
    return DocResult(email, method, notes, sha)
