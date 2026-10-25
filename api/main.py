"""HTTP API for the dashboard. Owner: A/D (started by B). Reads the event log; never edits it
except through replay and the attack console, which only append.

    make api        # uvicorn api.main:app --reload --port 8000

GET  /requests?month=6     one month's pay requests with a plain-English status
GET  /requests/{id}        one request: checklist, lines with video estimates, trace, Jev's review
GET  /events?after=0       Server-Sent Events from the log (after=-1: from now on; follow=false to just dump)
GET  /log?limit=100        the log as JSON, newest first, with a sentence per row (&before=seq, &kind=, &request_id=)
GET  /scorecard            data/scorecard.json from `make fuzz`
GET  /calibration          data/calibration.json from `python -m fuzzer.calibration` (else the committed report)
GET  /attacks              what the attack console offers
POST /attacks              run one attack: {"name", "target", "settings"}
POST /attacks/describe     a judge's plain-English attack, mapped by Claude: {"text"}
POST /sync                 copy the bank's ledger (Nessie: owner draws, sub payments) into the log
POST /replay?through=6     sync the bank, then decide every undecided request through that month; Jev answers
                           first whenever a model is configured (&judge=false: rules only; &redo=true: decide
                           every unpaid request again)
POST /pay-apps             upload a sub's pay application PDF (form: file, sub_id, job_id) -> a pay_requests fact
POST /site-video           upload the super's walkthrough video (form: file, job_id) -> a site_evidence fact
POST /releases             upload a lien release PDF (form: file, sub_id, job_id) -> a releases fact
POST /cois                 upload a certificate of insurance PDF (form: file, sub_id) -> a cois fact
POST /emails               upload an email (.eml) the AP inbox received (form: file) -> an emails fact
POST /setup/demo          load the demo company into an empty log and decide May-October (one time)
POST /ingest              read a document the browser uploaded to the private Blob store: {kind, pathname, sub_id?, job_id?}
POST /requests/{id}/approve  send an escalated payment to the approval key; on a press, the executor pays
POST /requests/{id}/pay    pay ({"confirm": true}): a released request pays now; one needing an OK goes to the key
POST /requests/{id}/resolve  settle a payment whose outcome is unknown by asking Nessie
POST /vendors/{id}/verify  check a vendor with CRS (rule 09) and record the status

Env: CORS_ORIGINS (comma-separated web origins, default http://localhost:3000), DEVICE_URL (approval key),
APPROVAL_KEY_PUBKEY (optional override of contract.yaml's esp32_pubkey, for a local mock key),
DATABASE_URL (Postgres log, deployed), BLOB_* (private Blob store for uploaded originals, deployed).
"""
import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from events import log as events
from events.replay import double_payments, in_month, month_end, replay, sync_ledger
from fuzzer import run as fuzzer
from shared.contract import load_contract
from shared.schema import Action, Decision, LedgerKind

DB = events.DEFAULT_PATH
# Working files. On Vercel only /tmp is writable (and it doesn't last): the log is Postgres there,
# and uploaded originals are archived to the private Blob store.
DATA = Path(os.environ.get("DRAW_DATA_DIR") or ("/tmp/draw" if os.environ.get("VERCEL") else "data"))
SCORECARD = DATA / "scorecard.json"
CALIBRATION = DATA / "calibration.json"
# The last real calibration run, committed: it takes thousands of Jev calls, so a deployment can't
# score it on a cold start the way it scores the attack scorecard.
CALIBRATION_REPORT = Path(__file__).resolve().parents[1] / "fuzzer" / "results" / "calibration.json"
UPLOADS = DATA / "uploads"
FRAMES = DATA / "frames"
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024

app = FastAPI(title="Draw")
ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])
ESCALATION_RULES = {"14_within_spend_authority", "15_confidence_floor"}  # kernel.runner's; failing them escalates

STATUS = {Action.release: "ready", Action.escalate: "needs_ok", Action.hold: "on_hold"}


@app.exception_handler(events.LogNotConfigured)
def log_not_configured(request: Request, exc: events.LogNotConfigured):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def _jev_route() -> str:
    from urllib.parse import urlsplit
    from judgment.jev import GATEWAY_URL
    return urlsplit(os.environ.get("JEV_BASE_URL", GATEWAY_URL)).hostname or "?"


def _jev_ready() -> bool:
    """Whether the key the active Jev route needs is set (TypeSafe, AI Gateway, or thejevai), never the key."""
    from judgment.jev import JevError, _config
    try:
        _config()
        return True
    except JevError:
        return False


def _fingerprint(value: str | None) -> str | None:
    """A short hash, so two copies of Draw can be compared (same database? same bank account?) without
    showing either value."""
    return hashlib.sha256(value.encode()).hexdigest()[:8] if value else None


def _judge_model():
    """judgment.judge when a model can answer (Jev, or Claude as its fallback); None means rules only.
    With no model at all, judging would only escalate everything (rule 15 sees zero confidence)."""
    if not (_jev_ready() or os.environ.get("ANTHROPIC_API_KEY")):
        return None
    from judgment import judge
    return judge


@app.get("/")
def status():
    """What's configured on this deployment, as yes/no only (never a value), plus whether the log has data."""
    from urllib.parse import urlsplit
    env = lambda *names: any(os.environ.get(n) for n in names)
    db_url = os.environ.get("DATABASE_URL")
    log = "postgres" if db_url else ("not connected" if os.environ.get("VERCEL") else "sqlite")
    try:
        has_events = not events.is_empty(DB)
    except Exception as e:  # report, don't crash the status page
        has_events = f"error: {type(e).__name__}"
    db = urlsplit(db_url) if db_url else None
    return {
        "service": "Draw API", "docs": "/docs", "event_log": log, "log_has_events": has_events,
        "version": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "")[:7] or None,
        # equal on two copies means they read and write the same log / pay from the same bank account
        "log_fingerprint": _fingerprint(f"{db.hostname}{db.path}") if db else None,
        "bank_fingerprint": _fingerprint(os.environ.get("NESSIE_ACCOUNT_ID")),
        "configured": {
            "anthropic_key": env("ANTHROPIC_API_KEY"), "jev_key": _jev_ready(), "jev_route": _jev_route(),
            "judgment": "jev" if _jev_ready() else "claude" if env("ANTHROPIC_API_KEY") else "rules only",
            "nessie_key": env("NESSIE_API_KEY"), "nessie_account": env("NESSIE_ACCOUNT_ID"),
            "crs": env("CRS_API_KEY") or (env("CRS_USERNAME") and env("CRS_PASSWORD")),
            "blob_store": env("BLOB_STORE_ID", "BLOB_READ_WRITE_TOKEN"),
            "cors_origins": os.environ.get("CORS_ORIGINS", "http://localhost:3000 (default)"),
            "device_url": env("DEVICE_URL"), "approval_pubkey": bool(_approval_key()[1]),
            "upload_passcode": env("UPLOAD_PASSCODE"), "inbound_email": env("INBOUND_PASSWORD"),
        },
    }


def decisions() -> dict[str, Decision]:
    """Latest kernel trace per request."""
    return {e.request_id: Decision.model_validate(e.data) for e in events.read(kind="kernel_trace", path=DB)}


def payments(state=None) -> dict[str, dict]:
    """Paid requests: this log's own receipts, then the bank ledger's sub payments (past months, and
    payments another copy of Draw made from the same Nessie account, once synced)."""
    paid = {e.request_id: {**e.data, "at": e.ts.isoformat()} for e in events.read(kind="execution", path=DB)
            if e.data.get("status", "paid") == "paid"}
    for entry in (state or events.state(path=DB))["ledger"]:
        if entry.kind == LedgerKind.sub_payment and entry.request_id and entry.request_id not in paid:
            paid[entry.request_id] = {"status": "paid", "amount": str(entry.amount), "at": entry.date.isoformat(),
                                      "source": "bank ledger"}
    return paid


WHY = {"over_limit": "it's over the auto-release limit", "unsure": "the automatic review wasn't sure",
       "new_vendor": "it's the first payment to this sub", "new_bank": "the bank account is new"}


def escalation_codes(req, state, decision: Decision) -> list[str]:
    """Why the owner's tap is needed: over the limit, unsure review, new sub, new bank account."""
    codes = []
    failed = {r.rule for r in decision.results if not r.passed}
    if "14_within_spend_authority" in failed:
        codes.append("over_limit")
    if "15_confidence_floor" in failed:
        codes.append("unsure")
    payments = [e for e in state["ledger"] if e.kind == "sub_payment" and e.sub_id == req.sub_id]
    if not payments:
        codes.append("new_vendor")
    elif req.bank_account != max(payments, key=lambda e: e.date).bank_account:
        codes.append("new_bank")
    return codes


def escalation_reason(req, state, decision: Decision) -> str:
    """The same reasons, in words."""
    return " and ".join(WHY[c] for c in escalation_codes(req, state, decision)) or "the rules asked for a person to look"


def sentence(decision: Decision | None, vendor: str, amount: Decimal, reason: str | None = None) -> str:
    """Every status is a sentence an office manager would say (CLAUDE.md UI copy rule)."""
    if decision is None:
        return "Not checked yet."
    if decision.action == Action.release:
        return "Ready to pay."
    if decision.action == Action.escalate:
        return f"Needs your OK because {reason}." if reason else f"Needs your OK: ${amount:,.2f} to {vendor.rstrip('.')}."
    failing = next((r for r in decision.results if not r.passed and r.rule not in ESCALATION_RULES), None)
    return f"On hold: {failing.message}" if failing else "On hold."


SOV_FROM_PAY_APP = "sc_from_pay_app_"  # Subcontract id prefix: SOV read from the sub's first pay app, not a signed contract
NEW_SUB_NOTE = ("New subcontractor. Their contract amounts were taken from this pay application; "
                "check them against the signed subcontract before approving.")


def new_sub(state, req) -> bool:
    return any(c.id.startswith(SOV_FROM_PAY_APP) and c.sub_id == req.sub_id and c.job_id == req.job_id
               for c in state["subcontracts"])


def row(req, state, decision, paid=None):
    vendor_record = next(v for v in state["vendors"] if v.id == req.sub_id)
    vendor = vendor_record.legal_name
    fresh = new_sub(state, req)
    amount = req.g702.current_payment_due
    escalated = decision is not None and decision.action == Action.escalate and not paid
    reason = escalation_reason(req, state, decision) if escalated else None
    return {
        "id": req.id, "sub_id": req.sub_id, "sub": vendor, "job_id": req.job_id,
        "job": next(j.name for j in state["jobs"] if j.id == req.job_id),
        "amount": str(amount), "period_end": str(req.period_end),
        "status": "paid" if paid else (STATUS[decision.action] if decision else "not_checked"),
        "sentence": f"Paid ${amount:,.2f} to {vendor.rstrip('.')}." if paid else sentence(decision, vendor, amount, reason),
        "reason": reason,
        "next_action": None if paid else (decision.next_action if decision else None),
        "new_sub": fresh,
        "new_sub_note": NEW_SUB_NOTE if fresh and not paid else None,
        # for the dashboard's colour categories (web/lib/labels.ts) and short phrases
        "failed_rules": sorted(r.rule[:2] for r in decision.results if not r.passed) if decision else [],
        "crs_status": vendor_record.crs_status,
        "limit": str(load_contract().get("auto_release_limit_usd", 0)),
        "why": escalation_codes(req, state, decision) if escalated else [],
    }


@app.get("/requests")
def list_requests(month: int = 6):
    """One month's requests: every pay application whose period ends in that calendar month."""
    state, ds = events.state(path=DB), decisions()
    ps = payments(state)
    return [row(r, state, ds.get(r.id), ps.get(r.id)) for r in state["pay_requests"] if in_month(r.period_end, month)]


@app.get("/requests/{request_id}")
def get_request(request_id: str):
    state = events.state(path=DB)
    req = next((r for r in state["pay_requests"] if r.id == request_id), None)
    if req is None:
        raise HTTPException(404, f"no pay request {request_id}")
    decision = decisions().get(request_id)
    review = next((e.data for e in reversed(events.read(kind="judgment", request_id=request_id, path=DB))), None)
    site = max((s for s in state["site_evidence"] if s.job_id == req.job_id and s.date <= req.period_end),
               key=lambda s: s.date, default=None)
    return {
        **row(req, state, decision, payments(state).get(request_id)),
        # Jev's latest answers (source, proposal, each answer with its confidence), behind "Show details"
        "judgment": {k: review.get(k) for k in ("source", "proposal", "answers")} if review else None,
        "checklist": [{"rule": r.rule, "passed": r.passed, "message": r.message} for r in decision.results]
        if decision else [],
        "lines": [{**l.model_dump(mode="json"),
                   "video_pct": str(site.estimates[l.item_no]) if site and l.item_no in site.estimates else None}
                  for l in req.lines],
        "site_evidence": {**site.model_dump(mode="json"), "sample": not READ_VIDEO.search(site.id)} if site else None,
        "bank_account": req.bank_account,
        "vendor": next({"legal_name": v.legal_name, "email_domain": v.email_domain}
                       for v in state["vendors"] if v.id == req.sub_id),
        "source_doc": req.source_doc,
        # read from an uploaded file (named by its hash) or seeded; seeded numbers are labelled on screen
        "sample_document": not HASHED.fullmatch(Path(req.source_doc).name),
        "original": Path(req.source_doc).name if HASHED.fullmatch(Path(req.source_doc).name) else None,
        "trace": decision.model_dump(mode="json") if decision else None,  # behind "Show details"
    }


@app.get("/requests/{request_id}/events")
def request_events(request_id: str):
    """The request's story from the append-only log, as summaries for the detail pane's timeline and
    review panel. Raw payloads never leave: pay-request facts and signatures carry bank accounts."""
    log = events.read(path=DB)
    fact = next((e for e in log if e.kind == "fact" and e.table == "pay_requests" and e.data.get("id") == request_id), None)
    if fact is None:
        raise HTTPException(404, f"no pay request {request_id}")
    when = lambda e: {"seq": e.seq, "at": e.at.isoformat(), "ts": e.ts.isoformat()}
    steps = [{"kind": "received", **when(fact), "read_from_file": bool(HASHED.fullmatch(Path(fact.data.get("source_doc", "")).name))}]
    amount = None
    for e in (x for x in log if x.request_id == request_id):
        if e.kind == "judgment":
            answers = {q: {"value": a.get("value"), "confidence": a.get("confidence")} for q, a in e.data.get("answers", {}).items()}
            steps.append({"kind": "reviewed", **when(e), "source": e.data.get("source"), "answers": answers,
                          "errors": len(e.data.get("errors") or [])})
        elif e.kind == "kernel_trace":
            failed = sorted(r["rule"][:2] for r in e.data.get("results", []) if not r.get("passed"))
            last = steps[-1]
            if last["kind"] == "checked" and last["action"] == e.data.get("action") and last["failed_rules"] == failed:
                last["times"] += 1  # replays that came to the same answer
            else:
                steps.append({"kind": "checked", **when(e), "action": e.data.get("action"), "failed_rules": failed, "times": 1})
        elif e.kind == "signature":
            steps.append({"kind": "signed", **when(e), "rejected": bool(e.data.get("rejected"))})
        elif e.kind == "execution":
            status = e.data.get("status", "paid")
            if status == "started":
                amount = e.data.get("amount")
            elif status == "failed":
                steps.append({"kind": "payment_failed", **when(e), "amount": amount})
            elif status == "paid":
                steps.append({"kind": "paid", **when(e), "amount": amount})
    threshold = load_contract().get("escalation_confidence_threshold")
    return {"steps": steps, "threshold": threshold}


@app.get("/events")
async def stream(request: Request, after: int = 0, follow: bool = True):
    after = int(request.headers.get("last-event-id", after))  # browsers resume where they left off
    if after < 0:  # "from now on": a dashboard opening shouldn't replay the whole history
        rows = events.read(path=DB)
        after = rows[-1].seq if rows else 0

    # Postgres is a network round trip per look (and keeps Neon from idling), so it's asked less often.
    every = 2.0 if os.environ.get("DATABASE_URL") else 0.5
    # On Vercel an open stream holds a function and its memory until the 300 s timeout, every tab,
    # nonstop; that exhausted the free plan. There it sends what's new and closes, and `retry` brings
    # the browser back in 10 s with Last-Event-ID: a cheap poll instead of a held connection.
    poll = bool(os.environ.get("VERCEL"))

    async def gen():
        if poll:
            yield "retry: 10000\n\n"
        last = after
        while True:
            for e in events.read(after=last, path=DB):
                last = e.seq
                yield f"id: {e.seq}\nevent: {e.kind}\ndata: {e.model_dump_json()}\n\n"
            if poll or not follow or await request.is_disconnected():
                return
            await asyncio.sleep(every)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/scorecard")
def scorecard():
    """`make fuzz` writes the full scorecard; with none on disk (a fresh deploy) a smaller one is
    scored on the spot and cached for this instance."""
    if SCORECARD.exists():
        return json.loads(SCORECARD.read_text())
    card = fuzzer.run(int(os.environ.get("SCORECARD_CASES", "2000")))
    try:
        SCORECARD.parent.mkdir(parents=True, exist_ok=True)
        SCORECARD.write_text(json.dumps(card))
    except OSError:
        pass  # read-only disk: serve it uncached
    return card


@app.get("/calibration")
def calibration():
    """This machine's latest run if there is one, else the committed report from the last real run."""
    for path in (CALIBRATION, CALIBRATION_REPORT):
        if path.exists():
            return json.loads(path.read_text())
    raise HTTPException(404, "no calibration yet; run `python -m fuzzer.calibration` (needs kernel + Jev/Claude)")


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
    except (ValueError, StopIteration, TypeError, ArithmeticError) as e:
        raise HTTPException(422, f"that attack can't run as described: {e}")
    if out["result"] is None:
        return {"plan": out["plan"], "result": None}
    r = out["result"]
    return {"plan": out["plan"], "label": r["label"].model_dump(mode="json"),
            "decision": r["decision"].model_dump(mode="json"), "amount": str(r["amount"]),
            "caught": r["decision"].action != Action.release, "caught_by_rules": r["decision"].action == Action.hold}


@app.post("/requests/{request_id}/approve")
def approve(request_id: str):
    """Send an escalated payment to the approval key and wait for a button (up to ~90 s).
    On approve the executor verifies the signature and pays; every refusal comes back as words.
    Same checks as /pay: decided again on current facts, paid once, bank account signed."""
    import time
    import uuid

    import requests as http

    from kernel import executor
    from shared.contract import load_contract

    state = events.state(path=DB)
    req = next((r for r in state["pay_requests"] if r.id == request_id), None)
    if req is None:
        raise HTTPException(404, f"no pay request {request_id}")
    stored = decisions().get(request_id)
    if stored is None or stored.action != Action.escalate:
        raise HTTPException(409, "Only a request that needs your OK goes to the approval key.")
    try:
        executor.check_payable(req, log_path=DB)
        executor.check_bank(req, log_path=DB)  # never ask for a tap on a payment the bank already has
    except executor.ExecutionRefused as e:
        return {"paid": False, "rejected": False, "reason": str(e)}
    decision = _fresh_decision(state, request_id, stored)
    if decision.action == Action.hold:
        failing = next((r for r in decision.results if not r.passed), None)
        raise HTTPException(409, f"On hold now: {failing.message if failing else 'a check failed'}")
    device, pubkey = _approval_key()
    if not device or not pubkey:
        raise HTTPException(503, "The approval key isn't set up yet (DEVICE_URL and the key's public key in config/contract.yaml).")
    amount = req.g702.current_payment_due
    payload = {"request_id": req.id, "payee": _vendor_name(state, req.sub_id) or req.sub_id, "sub_id": req.sub_id,
               "amount": str(amount), "job": req.job_id, "bank_account": req.bank_account,
               "nonce": uuid.uuid4().hex, "expires_at": int(time.time()) + 120}
    try:
        approval = executor.request_device_approval(device, payload)
    except http.exceptions.RequestException as e:  # unreachable, timed out (408), or busy (409): nothing was paid
        raise HTTPException(502, f"The approval key didn't answer: {type(e).__name__}. Nothing was paid.")
    try:
        result = executor.execute(decision, req, auto_release_limit_usd=Decimal(
            str(load_contract()["auto_release_limit_usd"])), pubkey_hex=pubkey, approval=approval, log_path=DB)
    except executor.ExecutionRefused as e:
        return {"paid": False, "rejected": approval.rejected, "reason": str(e)}
    return {"paid": True, "rejected": False, "result": result}


def _approval_key() -> tuple[str, str | None]:
    """Where the approval key listens (DEVICE_URL) and its registered public key (contract.yaml's esp32_pubkey,
    read fresh so a newly pasted key works without a restart; or APPROVAL_KEY_PUBKEY for a local mock)."""
    from shared.contract import load_contract
    url = os.environ.get("DEVICE_URL")
    pubkey = os.environ.get("APPROVAL_KEY_PUBKEY") or load_contract().get("esp32_pubkey") or None
    return (url.rstrip("/") if url else None), pubkey


class PayIn(BaseModel):
    confirm: bool  # a JSON body: browsers won't send one cross-site without CORS approval, so no drive-by payments


def _fresh_decision(state, request_id: str, stored: Decision) -> Decision:
    """Decide again on what is known now; facts can arrive after the stored decision (a lapsed COI, a
    bank-change email). Jev answers again when a model is configured, as in replay. A new hold wins;
    an escalation on either side keeps the owner's sign-off."""
    import pipeline
    model, review = _judge_model(), None
    if model is not None:
        fresh, review = pipeline.judge_and_decide(state, request_id, judge=model)
    else:
        fresh = pipeline.decide(state, request_id)
    if fresh.action != stored.action or [r.passed for r in fresh.results] != [r.passed for r in stored.results]:
        if review is not None:
            events.append("judgment", pipeline.judgment_event(review), request_id=request_id, path=DB)
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

    from kernel import executor
    from shared.contract import load_contract

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
    limit = Decimal(str(load_contract().get("auto_release_limit_usd", 0)))
    amount = req.g702.current_payment_due
    needs_ok = decision.action == Action.escalate or amount > limit or any(not r.passed for r in decision.results)

    approval, pubkey = None, None
    if needs_ok:
        device, pubkey = _approval_key()
        if not device or not pubkey:
            raise HTTPException(503, "The approval key isn't set up yet (DEVICE_URL and the key's public key in "
                                     "config/contract.yaml).")
        try:
            executor.check_bank(req, log_path=DB)  # never ask for a tap on a payment the bank already has
        except executor.ExecutionRefused as e:
            raise HTTPException(409, f"Not paid: {e}")
        # The owner signs the destination too, so a bank change can't ride on an approval for the old account.
        payload = {"request_id": req.id, "payee": _vendor_name(state, req.sub_id) or req.sub_id,
                   "sub_id": req.sub_id, "amount": str(amount), "job": req.job_id,
                   "bank_account": req.bank_account, "nonce": uuid.uuid4().hex,
                   "expires_at": int(time.time()) + 120}  # outlives the device's 90 s wait
        try:
            approval = executor.request_device_approval(device, payload)
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


def _require_passcode(given: str | None) -> None:
    """Same rule as the dashboard's upload route (web/app/api/blob-upload): UPLOAD_PASSCODE when it's
    set; with none set, only outside Vercel (local development)."""
    want = os.environ.get("UPLOAD_PASSCODE")
    if not want:
        if os.environ.get("VERCEL"):
            raise HTTPException(401, "Adding subcontractors is off until UPLOAD_PASSCODE is set on the server.")
        return
    if not hmac.compare_digest((given or "").encode(), want.encode()):
        raise HTTPException(401, "That upload passcode isn't right.")


HASHED = re.compile(r"[0-9a-f]{64}\.(pdf|eml)")  # an upload's stored name (_save_upload)
READ_VIDEO = re.compile(r"_\d{8}_[0-9a-f]{8}$")  # perception/video.py's evidence ids; seeded ones end in a month number


@app.get("/files/{name}")
def original_file(name: str, x_upload_passcode: str | None = Header(None)):
    """An original upload, for the detail pane's "Open the original". Pay apps carry full bank
    accounts and the dashboard has no logins, so it takes the upload passcode."""
    if not HASHED.fullmatch(name):
        raise HTTPException(404, "no such file")
    _require_passcode(x_upload_passcode)
    media = "application/pdf" if name.endswith(".pdf") else "message/rfc822"
    headers = {"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"}
    if os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID"):
        from vercel.blob import sync as blob
        try:  # opened before the response starts, so a missing file is still a 404
            opened = blob.stream_blob(f"uploads/{name}", access="private")
            download = opened.__enter__()
        except blob.BlobNotFoundError:
            raise HTTPException(404, "That original isn't in storage.")

        def chunks():
            try:
                yield from download
            finally:
                opened.__exit__(None, None, None)
        return StreamingResponse(chunks(), media_type=media, headers=headers)
    path = UPLOADS / name
    if not path.exists():
        raise HTTPException(404, "That original isn't on this server.")
    return FileResponse(path, media_type=media, headers=headers)


@app.get("/emails")
def list_emails(month: int = 6):
    """The month's email, newest first, for "Waiting for documents". The sub is matched on the exact
    domain only (perception.attachments.sender_sub), so a lookalike shows as unknown."""
    from perception.attachments import sender_sub

    state, end = events.state(path=DB), month_end(month)
    out = []
    for e in sorted(state["emails"], key=lambda e: e.sent_at, reverse=True):
        if (e.sent_at.year, e.sent_at.month) != (end.year, end.month):
            continue
        v = sender_sub(state, e.from_domain)
        out.append({"id": e.id, "from_addr": e.from_addr, "from_domain": e.from_domain, "subject": e.subject,
                    "sent_at": e.sent_at.isoformat(), "sub_id": v.id if v else None, "sub": v.legal_name if v else None})
    return out


@app.get("/vendors")
def list_vendors():
    """Every known sub (not only those billing this month), with the jobs they have a subcontract on."""
    state = events.state(path=DB)
    jobs = {}
    for c in state["subcontracts"]:
        jobs.setdefault(c.sub_id, []).append(c.job_id)
    return {"vendors": sorted(({"id": v.id, "legal_name": v.legal_name, "job_ids": jobs.get(v.id, [])}
                               for v in state["vendors"]), key=lambda v: v["legal_name"].lower()),
            "jobs": [{"id": j.id, "name": j.name} for j in state["jobs"]]}


class VendorIn(BaseModel):
    legal_name: str
    email_domain: str = ""
    ein: str = ""
    bank_account: str = ""


@app.post("/vendors")
def create_vendor(body: VendorIn, x_upload_passcode: str | None = Header(None)):
    """A new sub, before their first pay application. CRS status starts unknown, so rule 09 holds
    them until the identity check passes; with no payments yet, the first payment escalates."""
    from shared.schema import Vendor

    _require_passcode(x_upload_passcode)
    name = " ".join(body.legal_name.split())
    if not name:
        raise HTTPException(422, "Enter the subcontractor's legal name.")
    state = events.state(path=DB)
    if any(v.legal_name.strip().lower() == name.lower() for v in state["vendors"]):
        raise HTTPException(409, f"{name} is already a subcontractor. Pick them from the list.")
    vendor = Vendor(id=f"sub_{secrets.token_hex(4)}", legal_name=name, ein=body.ein.strip(),
                    email_domain=body.email_domain.strip().lower().removeprefix("@"),
                    bank_account=body.bank_account.strip(), first_paid=None, crs_status="unknown")
    events.append("fact", vendor, table="vendors", at=datetime.now().replace(microsecond=0), path=DB)
    return {"id": vendor.id, "legal_name": vendor.legal_name}


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


@app.post("/setup/demo")
def setup_demo():
    """Load the demo company into an EMPTY log and decide May-October, on the deployment itself, so
    nobody has to copy the database URL to a laptop. Refuses if the log has anything in it: it can
    never overwrite or mix with real data."""
    from events.replay import load_seed
    from seed import demo_company
    from seed.anomalies import with_demo
    from seed.generate import generate
    if not events.is_empty(DB):
        raise HTTPException(409, "The event log already has data; the demo company only loads into an empty log.")
    facts = load_seed(demo_company.add(with_demo(generate())[0]), path=DB)
    bank = _sync_bank()
    model = _judge_model()
    return {"facts_loaded": facts, "bank": bank, "judged": model is not None,
            "decisions": len(replay(6, path=DB, judge=model))}


def _sync_bank() -> dict:
    """Bring the bank's ledger into the log; never fails the caller (the answer says what happened)."""
    from kernel import executor
    if not (os.environ.get("NESSIE_API_KEY") and os.environ.get("NESSIE_ACCOUNT_ID")):
        return {"skipped": "Nessie isn't configured (NESSIE_API_KEY, NESSIE_ACCOUNT_ID)"}
    try:
        entries, skipped = executor.nessie_ledger()
    except executor.NessieError as e:
        return {"error": f"Couldn't read the bank: {e}"}
    return {"in_bank": len(entries), "new_to_log": sync_ledger(entries, path=DB), "skipped_rows": skipped,
            "paid_twice": double_payments(entries)}


@app.post("/sync")
def sync():
    """Copy the bank's ledger into the log: owner draws (rule 08) and sub payments, including ones another
    copy of Draw made from the same Nessie account. Each payment is added once."""
    if events.is_empty(DB):
        raise HTTPException(409, "The event log is empty; load the demo company first (POST /setup/demo).")
    bank = _sync_bank()
    if "skipped" in bank:
        raise HTTPException(503, bank["skipped"])
    if "error" in bank:
        raise HTTPException(502, bank["error"])
    return bank


@app.post("/replay")
def run_replay(through: int = 6, judge: bool | None = None, redo: bool = False):
    if events.is_empty(DB):
        raise HTTPException(409, "no events yet; run `make events` (or `python -m events load` against Postgres) first")
    bank = _sync_bank()
    model = _judge_model() if judge is not False else None
    if judge and model is None:
        raise HTTPException(503, "No model is configured to review requests (a Jev key, or ANTHROPIC_API_KEY).")
    return {"new_decisions": len(replay(through, path=DB, judge=model, redo=redo)), "judged": model is not None,
            "bank": bank}


# How each row of the log reads on the Log tab. Names come from the log itself (vendors, jobs).
def _usd(value) -> str:
    try:
        return f"${Decimal(str(value)):,.2f}"
    except ArithmeticError:
        return f"${value}"


def _log_line(e, names: dict[str, str]) -> str:
    d, who = e.data, lambda sid: names.get(sid, sid or "?")
    if e.kind == "fact":
        t = e.table
        if t == "pay_requests":
            due = (d.get("g702") or {}).get("current_payment_due")
            return f"Pay application #{d.get('number')} from {who(d.get('sub_id'))} came in: {_usd(due)} due."
        if t == "ledger":
            if d.get("kind") == "owner_draw":
                return f"The owner's draw for {who(d.get('job_id'))} cleared: {_usd(d.get('amount'))}."
            return f"The bank paid {who(d.get('sub_id'))} {_usd(d.get('amount'))} (request {d.get('request_id')})."
        if t == "vendors":
            return f"{d.get('legal_name')} is on file; identity check: {d.get('crs_status')}."
        if t == "releases":
            return f"Lien release from {who(d.get('sub_id'))}: {str(d.get('kind', '')).replace('_', ' ')}, {_usd(d.get('amount'))}."
        if t == "cois":
            return f"Insurance certificate for {who(d.get('sub_id'))} (workers' comp until {d.get('wc_expiry')})."
        if t == "emails":
            return f"Email from {d.get('from_addr')}: {d.get('subject') or '(no subject)'}."
        if t == "site_evidence":
            return f"Site video for {who(d.get('job_id'))} from {d.get('date')}."
        if t == "lower_tier_notices":
            return f"{d.get('supplier')} filed a notice to owner on {who(d.get('sub_id'))}'s work."
        return f"{t.replace('_', ' ').capitalize()} on file: {d.get('id')}."
    if e.kind == "judgment":
        if d.get("source") == "unavailable":
            return "No model could review it."
        reviewer = "Jev" if d.get("source") == "jev" else str(d.get("source", "")).capitalize()
        return f"{reviewer} reviewed it and suggested: {(d.get('proposal') or {}).get('action')}."
    if e.kind == "kernel_trace":
        failed = [r.get("rule", "")[:2] for r in d.get("results", []) if not r.get("passed")]
        return f"The rules decided: {d.get('action')}" + (f" (didn't pass: {', '.join(failed)})." if failed else ".")
    if e.kind == "signature":
        return f"Signed on the approval key for {_usd((d.get('payload') or {}).get('amount'))}."
    status = d.get("status", "paid")
    return {"started": f"Sent {_usd(d.get('amount'))} to the bank.",
            "paid": f"Paid {_usd(d.get('amount'))}" + (" (found in the bank)." if d.get("found_in_bank") else "."),
            "failed": f"The bank didn't take the payment: {d.get('error')}",
            "resolved_unpaid": "Checked the bank: no payment was made, so it can be paid again."}.get(status, status)


@app.get("/log")
def log_page(limit: int = 100, before: int | None = None, kind: str | None = None, request_id: str | None = None):
    """The event log, newest first, a page at a time, each row with a plain sentence (the Log tab)."""
    if kind and kind not in events.KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(events.KINDS)}")
    limit = max(1, min(limit, 500))
    rows = events.read(kind=kind or None, request_id=request_id or None, path=DB)
    if before is not None:
        rows = [e for e in rows if e.seq < before]
    page = rows[-limit:][::-1]
    state = events.state(path=DB)
    names = {v.id: v.legal_name for v in state["vendors"]} | {j.id: j.name for j in state["jobs"]}
    return {"events": [{**e.model_dump(mode="json"), "line": _log_line(e, names)} for e in page],
            "more": len(rows) > len(page)}


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
    _archive(data, f"{sha}{suffix}")  # by hash, so GET /files can find any original from its record
    return path, sha


def _archive(data: bytes, name: str) -> str | None:
    """Keep the original in the private Blob store when one is connected (deployed). Pay apps carry
    bank details, so the store must be private: nothing here gets a public URL."""
    if not (os.environ.get("BLOB_READ_WRITE_TOKEN") or os.environ.get("BLOB_STORE_ID")):
        return None
    from vercel.blob import sync as blob
    try:
        return blob.put_blob(f"uploads/{name}", data, access="private", allow_overwrite=True).pathname
    except blob.BlobError as e:
        raise HTTPException(502, f"Couldn't store the original file ({type(e).__name__}); nothing was recorded.")


def _notes(notes) -> list[dict]:
    return [{"code": n.code, "message": n.message, "item_no": n.item_no} for n in notes]


def _require_subcontract(state, sub_id: str, job_id: str | None = None) -> None:
    if not any(c.sub_id == sub_id and (job_id is None or c.job_id == job_id) for c in state["subcontracts"]):
        raise HTTPException(404, f"no subcontract for {sub_id}" + (f" on {job_id}" if job_id else ""))


def _append_new(state, table: str, record, at) -> int:
    if any(r.id == record.id for r in state[table]):
        raise HTTPException(409, f"this exact file was already uploaded as {record.id}")
    return events.append("fact", record, table=table, at=at, path=DB)


class IngestIn(BaseModel):
    kind: str  # pay_app | release | coi | email | site_video
    pathname: str  # in the private Blob store, under uploads/
    sub_id: str | None = None
    job_id: str | None = None
    upload_date: date | None = None


INGEST_SUFFIX = {"pay_app": ".pdf", "release": ".pdf", "coi": ".pdf", "email": ".eml", "site_video": ".mp4"}


@app.post("/ingest")
def ingest(body: IngestIn):
    """Read a document the browser uploaded straight to the private Blob store (client upload,
    so large files like site video skip Vercel's 4.5 MB request limit), then run it through the
    same handler as a direct upload."""
    if body.kind not in INGEST_SUFFIX:
        raise HTTPException(422, f"unknown document kind {body.kind!r}")
    if not body.pathname.startswith("uploads/") or ".." in body.pathname:
        raise HTTPException(422, "pathname must be under uploads/")
    needs = {"pay_app": ("sub_id", "job_id"), "release": ("sub_id", "job_id"), "coi": ("sub_id",),
             "site_video": ("job_id",), "email": ()}[body.kind]
    missing = [f for f in needs if not getattr(body, f)]
    if missing:
        raise HTTPException(422, f"{body.kind} needs {', '.join(missing)}")

    from starlette.datastructures import UploadFile as StarletteUpload
    from vercel.blob import sync as blob
    limit = MAX_VIDEO_BYTES if body.kind == "site_video" else MAX_PDF_BYTES
    UPLOADS.mkdir(parents=True, exist_ok=True)
    local = UPLOADS / f"incoming-{secrets.token_hex(8)}"
    try:
        size = 0
        with blob.stream_blob(body.pathname, access="private") as download, local.open("wb") as out:
            for chunk in download:
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"file is larger than {limit // (1024 * 1024)} MB")
                out.write(chunk)
    except blob.BlobNotFoundError:
        raise HTTPException(404, f"no uploaded file at {body.pathname}")
    except blob.BlobError as e:
        raise HTTPException(502, f"Couldn't fetch the upload from storage ({type(e).__name__}).")

    name = body.pathname.rsplit("/", 1)[-1]
    with local.open("rb") as f:
        file = StarletteUpload(file=f, filename=name)
        try:
            if body.kind == "pay_app":
                return upload_pay_app(file=file, sub_id=body.sub_id, job_id=body.job_id, g702=None)
            if body.kind == "release":
                return upload_release(file=file, sub_id=body.sub_id, job_id=body.job_id)
            if body.kind == "coi":
                return upload_coi(file=file, sub_id=body.sub_id)
            if body.kind == "email":
                return upload_email(file=file)
            return upload_site_video(file=file, job_id=body.job_id, upload_date=body.upload_date)
        finally:
            local.unlink(missing_ok=True)


@app.post("/pay-apps")
def upload_pay_app(file: UploadFile = File(...), sub_id: str = Form(...), job_id: str = Form(...),
                   g702: UploadFile | None = File(None)):
    """Read a pay application into the log. Sub and job come from who sent it, never from the PDF.
    `g702` is the G702 summary when the sub sent it as a separate file."""
    from perception import ExtractionError, extract_pay_request

    state = events.state(path=DB)
    brand_new = (any(v.id == sub_id for v in state["vendors"])
                 and not any(c.sub_id == sub_id for c in state["subcontracts"])
                 and not any(e.sub_id == sub_id for e in state["ledger"]))
    job = next((j for j in state["jobs"] if j.id == job_id), None)
    if not (brand_new and job):
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
    if any(r.id == req.id for r in state["pay_requests"]):
        raise HTTPException(409, f"this exact file was already uploaded as {req.id}")
    if brand_new and job:  # read first, so an unreadable PDF leaves no subcontract behind
        from shared.schema import SOVLine, Subcontract
        events.append("fact", Subcontract(
            id=f"{SOV_FROM_PAY_APP}{sub_id}_{job_id}", sub_id=sub_id, job_id=job_id,
            sov=[SOVLine(item_no=l.item_no, description=l.description, scheduled_value=l.scheduled_value)
                 for l in req.lines], retainage_rate=job.retainage_rate), table="subcontracts", at=submitted, path=DB)
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
            "email": result.record.model_dump(mode="json"),
            "attachments": _email_attachments(path.read_bytes(), result.record)}


@app.post("/inbound/postmark")
async def inbound_postmark(request: Request):
    """Postmark's inbound webhook: each email to Draw's address arrives here as JSON and goes through
    /emails exactly like an uploaded .eml. Postmark sends basic auth from the webhook URL
    (https://postmark:<INBOUND_PASSWORD>@.../inbound/postmark); with no password set it's off."""
    import base64
    from email.message import EmailMessage
    from starlette.datastructures import UploadFile as StarletteUpload

    want = os.environ.get("INBOUND_PASSWORD")
    if not want:
        raise HTTPException(503, "Inbound email is off until INBOUND_PASSWORD is set.")
    try:
        user, _, given = base64.b64decode(request.headers.get("authorization", "")[6:]).decode().partition(":")
    except ValueError:
        user, given = "", ""
    if not (secrets.compare_digest(user, "postmark") & secrets.compare_digest(given.encode(), want.encode())):
        raise HTTPException(403, "wrong inbound credentials")  # 403 also tells Postmark not to retry

    body = await request.json()
    if body.get("RawEmail"):  # "Include raw email content" on: the message exactly as it arrived
        eml = body["RawEmail"].encode()
    else:
        msg = EmailMessage()
        for h in ("From", "To", "Subject", "Date"):
            if body.get(h):
                msg[h] = body[h]
        msg.set_content(body.get("TextBody") or "")
        for a in body.get("Attachments") or []:
            main_type, _, sub_type = (a.get("ContentType") or "application/octet-stream").partition("/")
            msg.add_attachment(base64.b64decode(a.get("Content") or ""), maintype=main_type,
                               subtype=sub_type or "octet-stream", filename=a.get("Name"))
        eml = bytes(msg)
    file = StarletteUpload(file=io.BytesIO(eml), filename=f"postmark-{body.get('MessageID', 'mail')}.eml")
    try:
        return upload_email(file=file)
    except HTTPException as e:
        if e.status_code in (409, 422):  # already have it / can't read it: retrying won't help, so 200
            return {"ignored": e.detail}
        raise


def _email_attachments(eml: bytes, email) -> list[dict]:
    """File each PDF attached to an email through the same handler as a direct upload. Which sub
    sent it comes only from an exact match of the sender's domain; anything unsure goes to a person."""
    from starlette.datastructures import UploadFile as StarletteUpload
    from perception import attachments as att

    found = att.pdf_attachments(eml)
    if not found:
        return []
    state = events.state(path=DB)
    vendor = att.sender_sub(state, email.from_domain)
    job = att.only_job(state, vendor.id) if vendor else None
    UPLOADS.mkdir(parents=True, exist_ok=True)
    files = []
    for name, data in found:
        p = UPLOADS / f"att-{hashlib.sha256(data).hexdigest()}.pdf"
        p.write_bytes(data)
        files.append((name, p, att.classify(p)))
    has_pay_app = any(k == "pay_app" for _, _, k in files)
    g702 = next((p for _, p, k in files if k == "g702"), None)

    def upload(path: Path, name: str):
        f = StarletteUpload(file=path.open("rb"), filename=name)
        return f

    out = []
    for name, path, kind in files:
        row = {"filename": name, "kind": kind}
        if kind is None:
            row |= {"status": "needs_person", "detail": "Couldn't tell what this document is from its title."}
        elif kind == "g702" and has_pay_app:
            row |= {"status": "used", "detail": "Read as the summary page of the pay application."}
        elif kind == "g702":
            row |= {"status": "needs_person", "detail": "A summary page (G702) came without its line items (G703)."}
        elif vendor is None:
            row |= {"status": "needs_person",
                    "detail": f"The sender's domain ({email.from_domain}) isn't exactly any sub's on file, so this "
                              "wasn't filed under anyone. If it looks like a sub's name, treat it as suspicious."}
        elif kind in ("pay_app", "release") and job is None:
            row |= {"status": "needs_person", "detail": f"{vendor.legal_name} works on more than one job; pick which."}
        else:
            try:
                if kind == "pay_app":
                    result = upload_pay_app(file=upload(path, name), sub_id=vendor.id, job_id=job,
                                            g702=upload(g702, g702.name) if g702 else None)
                elif kind == "release":
                    result = upload_release(file=upload(path, name), sub_id=vendor.id, job_id=job)
                else:
                    result = upload_coi(file=upload(path, name), sub_id=vendor.id)
                row |= {"status": "recorded", "sub_id": vendor.id, "sub": vendor.legal_name, "result": result}
            except HTTPException as e:
                row |= {"status": "duplicate" if e.status_code == 409 else "unreadable", "detail": e.detail}
        out.append(row)
    return out


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
