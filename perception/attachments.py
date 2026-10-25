"""PDF attachments on an email the AP inbox received: pull them out, say what each one is, and
say which sub sent them. Owner: B (perception's extractors do the reading).

Two rules keep this safe:
- What a PDF is comes from its own printed form title, read by code. Nothing is guessed; a PDF
  with no readable title (a scan, an invoice we don't know) goes to a person.
- Which sub sent it comes only from an EXACT match between the sender's domain and a sub's domain
  on file. A lookalike domain (capital I for l) matches nobody: that is the $1.5M wire story.
"""
import re
from email import policy
from email.parser import BytesParser

MAX_ATTACHMENTS = 10
MAX_BYTES = 32 * 1024 * 1024

# Printed form titles, checked in order: a G703 names the G702 in its header ("AIA Document G702,
# APPLICATION AND CERTIFICATION FOR PAYMENT, containing..."), so the continuation sheet comes first.
KINDS = [
    ("pay_app", ("CONTINUATION SHEET",)),
    ("g702", ("APPLICATION AND CERTIFICATE FOR PAYMENT", "APPLICATION AND CERTIFICATION FOR PAYMENT",
              "APPLICATION SUMMARY (G702)")),
    ("release", ("WAIVER AND RELEASE", "LIEN RELEASE", "LIEN WAIVER")),
    ("coi", ("CERTIFICATE OF LIABILITY INSURANCE", "CERTIFICATE OF INSURANCE")),
]


# The AIA G702's own wording names the continuation sheet twice ("Continuation Sheet, AIA Document
# G703, is attached"; "Initial all figures on this Application and on the Continuation Sheet that
# are changed..."); neither makes the G702 a continuation sheet.
_G702_MENTIONS_G703 = re.compile(r"CONTINUATION SHEET,? AIA DOCUMENT G703,? IS ATTACHED|CONTINUATION SHEET THAT ARE")


def pdf_attachments(eml: bytes) -> list[tuple[str, bytes]]:
    """(filename, bytes) for each PDF attached to the message, in order; nothing else."""
    msg = BytesParser(policy=policy.default).parsebytes(eml)
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename() or ""
        if part.get_content_type() != "application/pdf" and not name.lower().endswith(".pdf"):
            continue
        data = part.get_payload(decode=True) or b""
        if data.startswith(b"%PDF") and len(data) <= MAX_BYTES:  # the bytes must really be a PDF
            out.append((name or f"attachment-{len(out) + 1}.pdf", data))
        if len(out) == MAX_ATTACHMENTS:
            break
    return out


def classify(path) -> str | None:
    """pay_app | g702 | release | coi from the PDF's printed title, or None if it can't tell."""
    import pdfplumber
    from perception.common import tolerance
    try:
        with pdfplumber.open(path) as pdf:
            text = " ".join((p.extract_text(x_tolerance=tolerance(p), y_tolerance=tolerance(p)) or "")
                            for p in pdf.pages[:2]).upper()
    except Exception:  # not a readable PDF: a person looks at it
        return None
    text = _G702_MENTIONS_G703.sub(" ", re.sub(r"\s+", " ", text))
    for kind, titles in KINDS:
        if any(t in text for t in titles):
            # a pay app that carries its own G702 summary is still one pay app
            return kind
    return None


def sender_sub(state, from_domain: str):
    """The vendor whose domain on file is exactly the sender's (case-insensitive), or None."""
    d = (from_domain or "").strip().lower()
    return next((v for v in state["vendors"] if v.email_domain.lower() == d), None) if d else None


def only_job(state, sub_id: str) -> str | None:
    """The sub's job when it has exactly one subcontract; with several, a person picks."""
    jobs = {c.job_id for c in state["subcontracts"] if c.sub_id == sub_id}
    return jobs.pop() if len(jobs) == 1 else None
