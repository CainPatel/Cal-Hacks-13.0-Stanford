"""HTTP API for the dashboard. Owner: A/D (started by B). Reads the event log; never edits it
except through replay and the attack console, which only append.

    make api        # uvicorn api.main:app --reload --port 8000

GET  /requests?month=6     one month's pay requests with a plain-English status
GET  /requests/{id}        one request: checklist, lines with video estimates, trace
GET  /events?after=0       Server-Sent Events from the log (follow=false to just dump)
GET  /scorecard            data/scorecard.json from `make fuzz`
GET  /calibration          data/calibration.json from `python -m fuzzer.calibration`
GET  /attacks              what the attack console offers
POST /attacks              run one attack: {"name", "target", "settings"}
POST /attacks/describe     a judge's plain-English attack, mapped by Claude: {"text"}
POST /replay?through=6     decide every undecided request through that month (&judge=true: Jev first)
POST /requests/{id}/pay    pay it ({"confirm": true}): released pays now, else via the approval key, then Nessie
POST /requests/{id}/resolve  settle a payment whose outcome is unknown by asking Nessie
POST /vendors/{id}/verify  check a vendor with CRS (rule 09) and record the status
POST /pay-apps             upload a sub's pay application PDF (form: file, sub_id, job_id) -> a pay_requests fact
POST /site-video           upload the super's walkthrough video (form: file, job_id) -> a site_evidence fact
POST /releases             upload a lien release PDF (form: file, sub_id, job_id) -> a releases fact
POST /cois                 upload a certificate of insurance PDF (form: file, sub_id) -> a cois fact
POST /emails               upload an email (.eml) the AP inbox received (form: file) -> an emails fact
"""
import asyncio
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from events import log as events
from events.replay import month_end, replay
from fuzzer import run as fuzzer
from shared.schema import Action, Decision

DB = events.DEFAULT_PATH
SCORECARD = Path("data/scorecard.json")
CALIBRATION = Path("data/calibration.json")
UPLOADS = Path("data/uploads")
FRAMES = Path("data/frames")
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024

app = FastAPI(title="Draw")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"])

STATUS = {Action.release: "ready", Action.escalate: "needs_ok", Action.hold: "on_hold"}
DEVICE_TIMEOUT_S = 100  # the device gives up after 90 s; allow for the round trip


def decisions() -> dict[str, Decision]:
    """Latest kernel trace per request."""
    return {e.request_id: Decision.model_validate(e.data) for e in events.read(kind="kernel_trace", path=DB)}


def payments() -> dict[str, dict]:
    """Successful executions per request (the bank's receipt)."""
    return {e.request_id: {**e.data, "at": e.ts.isoformat()} for e in events.read(kind="execution", path=DB)
            if e.data.get("status", "paid") == "paid"}


def sentence(decision: Decision | None, vendor: str, amount: Decimal) -> str:
    """Every status is a sentence an office manager would say (CLAUDE.md UI copy rule)."""
    if decision is None:
        return "Not checked yet."
    if decision.action == Action.release:
        return "Ready to pay."
    if decision.action == Action.escalate:
        return f"Needs your OK: ${amount:,.2f} to {vendor.rstrip('.')}."
    failing = next((r for r in decision.results if not r.passed), None)
    return f"On hold: {failing.message}" if failing else "On hold."


def row(req, state, decision, paid=None):
    vendor = next(v.legal_name for v in state["vendors"] if v.id == req.sub_id)
    amount = req.g702.current_payment_due
    return {
        "id": req.id, "sub_id": req.sub_id, "sub": vendor, "job_id": req.job_id,
        "job": next(j.name for j in state["jobs"] if j.id == req.job_id),
        "amount": str(amount), "period_end": str(req.period_end),
        "status": "paid" if paid else (STATUS[decision.action] if decision else "not_checked"),
        "sentence": f"Paid ${amount:,.2f} to {vendor.rstrip('.')}." if paid else sentence(decision, vendor, amount),
        "next_action": None if paid else (decision.next_action if decision else None),
    }


@app.get("/requests")
def list_requests(month: int = 6):
    state, ds, ps = events.state(path=DB), decisions(), payments()
    return [row(r, state, ds.get(r.id), ps.get(r.id)) for r in state["pay_requests"] if r.period_end == month_end(month)]


@app.get("/requests/{request_id}")
def get_request(request_id: str):
    state = events.state(path=DB)
    req = next((r for r in state["pay_requests"] if r.id == request_id), None)
    if req is None:
        raise HTTPException(404, f"no pay request {request_id}")
    decision = decisions().get(request_id)
    site = max((s for s in state["site_evidence"] if s.job_id == req.job_id and s.date <= req.period_end),
               key=lambda s: s.date, default=None)
    return {
        **row(req, state, decision, payments().get(request_id)),
        "checklist": [{"rule": r.rule, "passed": r.passed, "message": r.message} for r in decision.results]
        if decision else [],
        "lines": [{**l.model_dump(mode="json"),
                   "video_pct": str(site.estimates[l.item_no]) if site and l.item_no in site.estimates else None}
                  for l in req.lines],
        "site_evidence": site.model_dump(mode="json") if site else None,
        "bank_account": req.bank_account,
        "source_doc": req.source_doc,
        "trace": decision.model_dump(mode="json") if decision else None,  # behind "Show details"
    }


@app.get("/events")
async def stream(request: Request, after: int = 0, follow: bool = True):
    after = int(request.headers.get("last-event-id", after))  # browsers resume where they left off

    async def gen():
        last = after
        while True:
            for e in events.read(after=last, path=DB):
                last = e.seq
                yield f"id: {e.seq}\nevent: {e.kind}\ndata: {e.model_dump_json()}\n\n"
            if not follow or await request.is_disconnected():
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/scorecard")
def scorecard():
    if not SCORECARD.exists():
        raise HTTPException(404, "no scorecard yet; run `make fuzz`")
    return json.loads(SCORECARD.read_text())


@app.get("/calibration")
def calibration():
    if not CALIBRATION.exists():
        raise HTTPException(404, "no calibration yet; run `python -m fuzzer.calibration` (needs kernel + Jev/Claude)")
    return json.loads(CALIBRATION.read_text())


class AttackIn(BaseModel):
    name: str
    target: str  # a sub id, or a job id for new_sub_ein_mismatch
    settings: dict = {}


@app.get("/attacks")
def list_attacks():
    return [{"name": n, "about": (m.__doc__ or "").strip().splitlines()[0]} for n, m in fuzzer.ATTACKS.items()]


@app.post("/attacks")
def run_attack(body: AttackIn):
    if body.name not in fuzzer.ATTACKS:
        raise HTTPException(404, f"unknown attack {body.name}")
    try:
        out = fuzzer.attack(body.name, body.target, **body.settings)
    except (ValueError, StopIteration, TypeError) as e:
        raise HTTPException(422, f"{body.name} can't run on {body.target}: {e}")
    d = out["decision"]
    return {"label": out["label"].model_dump(mode="json"), "decision": d.model_dump(mode="json"),
            "amount": str(out["amount"]), "caught": d.action != Action.release,
            "caught_by_rules": d.action == Action.hold}


class DescribeIn(BaseModel):
    text: str


@app.post("/attacks/describe")
def describe_attack(body: DescribeIn):
    from fuzzer import nl_attack
    try:
        out = nl_attack.run(body.text)
    except nl_attack.AttackError as e:
        raise HTTPException(502, str(e))
    except (ValueError, StopIteration, TypeError) as e:
        raise HTTPException(422, f"that attack can't run as described: {e}")
    if out["result"] is None:
        return {"plan": out["plan"], "result": None}
    r = out["result"]
    return {"plan": out["plan"], "label": r["label"].model_dump(mode="json"),
            "decision": r["decision"].model_dump(mode="json"), "amount": str(r["amount"]),
            "caught": r["decision"].action != Action.release, "caught_by_rules": r["decision"].action == Action.hold}


@app.post("/replay")
def run_replay(through: int = 6, judge: bool = False):
    if not DB.exists():
        raise HTTPException(409, "no event log; run `make events` first")
    model = None
    if judge:
        from judgment import judge as model
    return {"new_decisions": len(replay(through, path=DB, judge=model))}


def _approval_key() -> tuple[str, str | None]:
    """Where the approval key listens, and its registered public key (contract.yaml; env overrides for local runs)."""
    import os

    import pipeline
    url = os.environ.get("ESP32_DEVICE_URL") or "http://localhost:8001"
    pubkey = os.environ.get("APPROVAL_KEY_PUBKEY") or pipeline.default_contract().get("esp32_pubkey") or None
    return url.rstrip("/"), pubkey


class PayIn(BaseModel):
    confirm: bool  # a JSON body: browsers won't send one cross-site without CORS approval, so no drive-by payments


def _fresh_decision(state, request_id: str, stored: Decision) -> Decision:
    """Decide again on what is known now; facts can arrive after the stored decision (a lapsed COI, a
    bank-change email). A new hold wins; an escalation on either side keeps the owner's sign-off."""
    import pipeline
    fresh = pipeline.decide(state, request_id)
    if fresh.action != stored.action or [r.passed for r in fresh.results] != [r.passed for r in stored.results]:
        events.append("kernel_trace", fresh, request_id=request_id, path=DB)
    if fresh.action == Action.hold or fresh.action == Action.escalate or stored.action != Action.escalate:
        return fresh
    return stored  # stored escalated (e.g. judgment wasn't sure) and fresh didn't: still needs the owner


@app.post("/requests/{request_id}/pay")
def pay(request_id: str, body: PayIn):
    """Move the money. A released request under the limit pays straight away; anything that needs the
    owner's OK goes to the approval key first and pays only on a valid signature (kernel/executor.py)."""
    import time
    import uuid

    import requests as http

    import pipeline
    from kernel import executor

    if not body.confirm:
        raise HTTPException(422, "Send {\"confirm\": true} to pay.")
    state = events.state(path=DB)
    req = next((r for r in state["pay_requests"] if r.id == request_id), None)
    if req is None:
        raise HTTPException(404, f"no pay request {request_id}")
    stored = decisions().get(request_id)
    if stored is None:
        raise HTTPException(409, "This request hasn't been checked yet; run the replay first.")
    try:
        executor.check_payable(req, log_path=DB)  # before the owner is asked to tap anything
    except executor.ExecutionRefused as e:
        raise HTTPException(409, f"Not paid: {e}")
    decision = _fresh_decision(state, request_id, stored)
    if decision.action == Action.hold:
        failing = next((r for r in decision.results if not r.passed), None)
        raise HTTPException(409, f"On hold: {failing.message if failing else ''} {decision.next_action or ''}".strip())
    limit = Decimal(str(pipeline.default_contract().get("auto_release_limit_usd", 0)))
    amount = req.g702.current_payment_due
    needs_ok = decision.action == Action.escalate or amount > limit or any(not r.passed for r in decision.results)

    approval, pubkey = None, None
    if needs_ok:
        device, pubkey = _approval_key()
        if not pubkey:
            raise HTTPException(409, "Register the approval key first: put its public key (GET /pubkey on the "
                                     "device) in config/contract.yaml as esp32_pubkey.")
        # The owner signs the destination too, so a bank change can't ride on an approval for the old account.
        payload = {"request_id": req.id, "payee": req.sub_id, "amount": str(amount), "job": req.job_id,
                   "bank_account": req.bank_account, "nonce": uuid.uuid4().hex,
                   "expires_at": int(time.time()) + 120}  # outlives the device's 90 s wait
        try:
            approval = executor.request_device_approval(device, payload, timeout=DEVICE_TIMEOUT_S)
        except http.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else 502
            raise HTTPException(504 if code == 408 else 409 if code == 409 else 502,
                                {408: "Nobody pressed the button in time.",
                                 409: "The approval key is already waiting on another payment."}.get(
                                     code, f"The approval key answered HTTP {code}."))
        except http.exceptions.RequestException as e:
            raise HTTPException(502, f"Can't reach the approval key at {device}: {type(e).__name__}")
        if approval.rejected:
            # A rejection can only stop a payment, so it is honored without checking the signature.
            raise HTTPException(403, "Rejected on the approval key. Nothing was paid.")
    try:
        receipt = executor.execute(decision, req, auto_release_limit_usd=limit, pubkey_hex=pubkey,
                                   approval=approval, log_path=DB)
    except executor.ExecutionRefused as e:
        raise HTTPException(409, f"Not paid: {e}")
    return {"paid": True, "request_id": req.id, "amount": str(amount), "approved_on_device": approval is not None,
            "receipt": receipt}


@app.post("/requests/{request_id}/resolve")
def resolve(request_id: str):
    """After an unknown bank outcome: look for the bill in Nessie and record what really happened."""
    from kernel import executor

    req = next((r for r in events.state(path=DB)["pay_requests"] if r.id == request_id), None)
    if req is None:
        raise HTTPException(404, f"no pay request {request_id}")
    try:
        outcome = executor.resolve_unknown(req, log_path=DB)
    except executor.ExecutionRefused as e:
        raise HTTPException(409, str(e))
    except executor.NessieError as e:
        raise HTTPException(502, f"Couldn't check the bank: {e}")
    return {"request_id": request_id, "outcome": outcome}


@app.post("/vendors/{vendor_id}/verify")
def verify_vendor(vendor_id: str):
    """Rule 09's identity check: CRS Business InstantID on the vendor's legal name and EIN."""
    import crs

    vendor = next((v for v in events.state(path=DB)["vendors"] if v.id == vendor_id), None)
    if vendor is None:
        raise HTTPException(404, f"no vendor {vendor_id}")
    updated, result, error = crs.check_vendor(vendor)
    if error:
        raise HTTPException(502, f"CRS check failed; status left as {vendor.crs_status!r}: {error}")
    if updated.crs_status != vendor.crs_status:
        events.append("fact", updated, table="vendors", path=DB)
    return {"vendor_id": vendor_id, "crs_status": result.status, "reasons": result.reasons, "evidence": result.evidence}


def _save_upload(upload: UploadFile, limit: int, suffix: str) -> tuple[Path, str]:
    """Store an upload under data/uploads by content hash; the name never comes from the client."""
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"file is larger than {limit // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "the file is empty")
    sha = hashlib.sha256(data).hexdigest()
    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{sha}{suffix}"
    path.write_bytes(data)
    return path, sha


def _notes(notes) -> list[dict]:
    return [{"code": n.code, "message": n.message, "item_no": n.item_no} for n in notes]


def _require_subcontract(state, sub_id: str, job_id: str | None = None) -> None:
    if not any(c.sub_id == sub_id and (job_id is None or c.job_id == job_id) for c in state["subcontracts"]):
        raise HTTPException(404, f"no subcontract for {sub_id}" + (f" on {job_id}" if job_id else ""))


def _append_new(state, table: str, record, at) -> int:
    if any(r.id == record.id for r in state[table]):
        raise HTTPException(409, f"this exact file was already uploaded as {record.id}")
    return events.append("fact", record, table=table, at=at, path=DB)


@app.post("/pay-apps")
def upload_pay_app(file: UploadFile = File(...), sub_id: str = Form(...), job_id: str = Form(...),
                   g702: UploadFile | None = File(None)):
    """Read a pay application into the log. Sub and job come from who sent it, never from the PDF.
    `g702` is the G702 summary when the sub sent it as a separate file."""
    from perception import ExtractionError, extract_pay_request

    state = events.state(path=DB)
    _require_subcontract(state, sub_id, job_id)
    path, sha = _save_upload(file, MAX_PDF_BYTES, ".pdf")
    g702_path = _save_upload(g702, MAX_PDF_BYTES, ".pdf")[0] if g702 is not None and g702.filename else None
    submitted = datetime.now().replace(microsecond=0)
    try:
        result = extract_pay_request(path, sub_id=sub_id, job_id=job_id, submitted_at=submitted, g702_pdf=g702_path)
    except ExtractionError as e:
        raise HTTPException(422, f"Couldn't read this pay application: {e}")
    req = result.pay_request
    req = req.model_copy(update={"id": f"pr_{sub_id}_{req.number}_{sha[:8]}"})
    seq = _append_new(state, "pay_requests", req, submitted)
    return {"request_id": req.id, "seq": seq, "method": result.method, "fallback_reason": result.fallback_reason,
            "notes": _notes(result.notes), "pay_request": req.model_dump(mode="json")}


def _vendor_name(state, sub_id: str) -> str | None:
    return next((v.legal_name for v in state["vendors"] if v.id == sub_id), None)


@app.post("/releases")
def upload_release(file: UploadFile = File(...), sub_id: str = Form(...), job_id: str = Form(...)):
    """A lien release the sub sent. It counts as received today."""
    from perception.common import ExtractionError
    from perception.release import extract_release

    state = events.state(path=DB)
    _require_subcontract(state, sub_id, job_id)
    path, sha = _save_upload(file, MAX_PDF_BYTES, ".pdf")
    today = date.today()
    try:
        result = extract_release(path, sub_id=sub_id, job_id=job_id, received_at=today,
                                 release_id=f"rel_{sub_id}_{sha[:10]}", expected_claimant=_vendor_name(state, sub_id))
    except ExtractionError as e:
        raise HTTPException(422, f"Couldn't read this release: {e}")
    seq = _append_new(state, "releases", result.record, today)
    return {"release_id": result.record.id, "seq": seq, "method": result.method, "notes": _notes(result.notes),
            "release": result.record.model_dump(mode="json")}


@app.post("/cois")
def upload_coi(file: UploadFile = File(...), sub_id: str = Form(...)):
    """A sub's certificate of insurance. The newest one uploaded is the one on file."""
    from perception.coi import extract_coi
    from perception.common import ExtractionError

    state = events.state(path=DB)
    _require_subcontract(state, sub_id)
    path, sha = _save_upload(file, MAX_PDF_BYTES, ".pdf")
    try:
        result = extract_coi(path, sub_id=sub_id, coi_id=f"coi_{sub_id}_{sha[:10]}",
                             expected_insured=_vendor_name(state, sub_id))
    except ExtractionError as e:
        raise HTTPException(422, f"Couldn't read this certificate: {e}")
    seq = _append_new(state, "cois", result.record, date.today())
    return {"coi_id": result.record.id, "seq": seq, "method": result.method, "notes": _notes(result.notes),
            "coi": result.record.model_dump(mode="json")}


@app.post("/emails")
def upload_email(file: UploadFile = File(...)):
    """An email the AP inbox received (.eml). Which sub it concerns is worked out downstream from its domain."""
    from perception.common import ExtractionError
    from perception.mail import extract_email

    state = events.state(path=DB)
    path, sha = _save_upload(file, 10 * 1024 * 1024, ".eml")
    try:
        result = extract_email(path, email_id=f"email_{sha[:12]}")
    except ExtractionError as e:
        raise HTTPException(422, f"Couldn't read this email: {e}")
    seq = _append_new(state, "emails", result.record, result.record.sent_at)
    return {"email_id": result.record.id, "seq": seq, "method": result.method, "notes": _notes(result.notes),
            "email": result.record.model_dump(mode="json")}


@app.post("/site-video")
def upload_site_video(file: UploadFile = File(...), job_id: str = Form(...), upload_date: date | None = Form(None)):
    """Turn a walkthrough video into site evidence for every line on the job's subcontracts."""
    from perception import VideoError, build_site_evidence

    state = events.state(path=DB)
    lines, seen = [], set()
    for c in state["subcontracts"]:  # the GC's contract SOV, not text from any sub's PDF
        if c.job_id == job_id:
            for s in c.sov:
                if s.item_no not in seen:
                    seen.add(s.item_no)
                    lines.append(s)
    if not lines:
        raise HTTPException(404, f"no subcontracts on {job_id}")
    suffix = Path(file.filename or "").suffix.lower()
    path, _ = _save_upload(file, MAX_VIDEO_BYTES, suffix if suffix in {".mp4", ".mov", ".m4v", ".webm"} else ".mp4")
    try:
        result = build_site_evidence(path, job_id=job_id, upload_date=upload_date or date.today(),
                                     line_items=lines, frames_dir=FRAMES)
    except (VideoError, ValueError) as e:
        raise HTTPException(422, f"Couldn't use this video: {e}")
    ev = result.evidence
    seq = events.append("fact", ev, table="site_evidence", at=ev.date, path=DB)
    return {"site_evidence_id": ev.id, "seq": seq, "date_source": result.date_source, "notes": _notes(result.notes),
            "site_evidence": ev.model_dump(mode="json")}
