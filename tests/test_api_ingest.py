"""Getting real documents into the log: pay application PDFs and site video, through the API."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from api import main
from events import log as events
from events.replay import load_seed
from perception import llm
from seed.anomalies import with_demo
from seed.generate import generate
from synthetic import render
from tests.perception.test_video import make_video


@pytest.fixture
def world():
    return with_demo(generate())[0]


@pytest.fixture
def client(tmp_path, monkeypatch, world):
    db = tmp_path / "events.db"
    load_seed(world, path=db)
    monkeypatch.setattr(main, "DB", db)
    monkeypatch.setattr(main, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(main, "FRAMES", tmp_path / "frames")
    return TestClient(main.app)


def rendered_pay_app(world, tmp_path, request_id="pr_sub_02_6", style="accounting"):
    req = next(r for r in world["pay_requests"] if r.id == request_id)
    vendor = next(v for v in world["vendors"] if v.id == req.sub_id)
    job = next(j for j in world["jobs"] if j.id == req.job_id)
    path = tmp_path / "g703.pdf"
    render.g703_pdf(req, vendor, job, path, style)
    return req, path


def upload(client, path, sub_id, job_id):
    with open(path, "rb") as f:
        return client.post("/pay-apps", files={"file": ("payapp.pdf", f, "application/pdf")},
                           data={"sub_id": sub_id, "job_id": job_id})


def test_pay_app_upload_becomes_a_fact(client, world, tmp_path):
    req, path = rendered_pay_app(world, tmp_path)
    r = upload(client, path, req.sub_id, req.job_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "text_layer" and body["request_id"].startswith(f"pr_{req.sub_id}_{req.number}_")
    stored = next(p for p in events.state(path=main.DB)["pay_requests"] if p.id == body["request_id"])
    assert stored.lines == req.lines and stored.g702 == req.g702 and stored.bank_account == req.bank_account
    assert (stored.sub_id, stored.job_id, stored.number) == (req.sub_id, req.job_id, req.number)


def test_uploaded_pay_app_is_not_sample_and_links_its_original(client, world, tmp_path):
    req, path = rendered_pay_app(world, tmp_path)
    rid = upload(client, path, req.sub_id, req.job_id).json()["request_id"]
    client.post("/replay")
    d = client.get(f"/requests/{rid}").json()
    assert d["sample_document"] is False and main.HASHED.fullmatch(d["original"])
    assert client.get(f"/files/{d['original']}").content == path.read_bytes()


def test_same_file_twice_is_refused(client, world, tmp_path):
    req, path = rendered_pay_app(world, tmp_path)
    assert upload(client, path, req.sub_id, req.job_id).status_code == 200
    assert upload(client, path, req.sub_id, req.job_id).status_code == 409


def test_unknown_sub_or_job_is_refused(client, world, tmp_path):
    req, path = rendered_pay_app(world, tmp_path)
    assert upload(client, path, "sub_nobody", req.job_id).status_code == 404
    assert upload(client, path, req.sub_id, "job_nowhere").status_code == 404


def test_unreadable_pdf_is_a_plain_error(client, world, tmp_path):
    req = next(r for r in world["pay_requests"] if r.id == "pr_sub_02_6")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    r = upload(client, bad, req.sub_id, req.job_id)
    assert r.status_code == 422 and r.json()["detail"].startswith("Couldn't read this pay application")


def test_uploaded_pay_app_is_decided_by_the_real_kernel(client, world, tmp_path):
    import pipeline
    if pipeline.kernel_decide is None:
        pytest.skip("needs kernel/")
    req, path = rendered_pay_app(world, tmp_path, request_id="pr_sub_07_6", style="bare")
    rid = upload(client, path, req.sub_id, req.job_id).json()["request_id"]
    client.post("/replay", params={"through": 6})
    detail = client.get(f"/requests/{rid}").json()
    assert detail["status"] != "not_checked" and detail["checklist"]
    # the upload is the same paperwork as the seed's own October request, so the kernel agrees with it
    assert detail["status"] == client.get(f"/requests/{req.id}").json()["status"]


def test_site_video_upload_becomes_evidence(client, world, tmp_path, monkeypatch):
    job_id = world["jobs"][0].id
    sov = [s for c in world["subcontracts"] if c.job_id == job_id for s in c.sov]
    seen = {}

    def fake_vision(frames, line_items, client=None):
        seen["items"] = line_items
        return llm.ProgressEstimate(estimates=[llm.ItemEstimate(item_no=sov[0].item_no, visible=True,
                                                                observations="slab poured", pct_complete=40.0,
                                                                confidence=0.9)])

    monkeypatch.setattr(llm, "estimate_progress", fake_vision)
    video = make_video(tmp_path / "walk.mp4")
    with open(video, "rb") as f:
        r = client.post("/site-video", files={"file": ("walk.mp4", f, "video/mp4")},
                        data={"job_id": job_id, "upload_date": "2026-10-25"})
    assert r.status_code == 200, r.text
    ev = next(s for s in events.state(path=main.DB)["site_evidence"] if s.id == r.json()["site_evidence_id"])
    assert ev.job_id == job_id and ev.date == date(2026, 10, 25) and ev.estimates == {sov[0].item_no: 40}
    # the prompt named the GC's SOV lines for the whole job, not text from any sub's PDF
    assert {i for i, _ in seen["items"]} == {s.item_no for s in sov}


def test_site_video_for_unknown_job(client, tmp_path):
    video = make_video(tmp_path / "walk.mp4")
    with open(video, "rb") as f:
        r = client.post("/site-video", files={"file": ("walk.mp4", f, "video/mp4")}, data={"job_id": "job_nowhere"})
    assert r.status_code == 404


def test_replay_can_put_judgment_in_the_loop(client, monkeypatch):
    calls = {}

    def fake_replay(through, path, judge=None, redo=False):
        calls.update(through=through, judge=judge, redo=redo)
        return []

    monkeypatch.setattr(main, "replay", fake_replay)
    client.post("/replay", params={"through": 6})
    assert calls["judge"] is None  # no model configured: rules only
    assert client.post("/replay", params={"through": 6, "judge": "true"}).status_code == 503
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")  # a model can answer: it reviews by default
    from judgment import judge
    client.post("/replay", params={"through": 6})
    assert calls["judge"] is judge
    client.post("/replay", params={"through": 6, "judge": "false"})
    assert calls["judge"] is None
    client.post("/replay", params={"through": 6, "redo": "true"})
    assert calls["redo"] is True


# --- releases, certificates, emails, separate G702 ------------------------------


def _post(client, url, path, data, name="doc.pdf", mime="application/pdf"):
    with open(path, "rb") as f:
        return client.post(url, files={"file": (name, f, mime)}, data=data)


def test_missing_release_upload_flips_the_kernel(tmp_path, monkeypatch):
    """Held for a missing conditional release; the sub uploads it; the real kernel now sees it."""
    import pipeline
    if pipeline.kernel_decide is None:
        pytest.skip("needs kernel/")
    world = with_demo(generate())[0]
    rid = "pr_sub_02_6"
    release = next(r for r in world["releases"] if r.id == f"rel_c_{rid}")
    world = {k: [r for r in v if r is not release] if k == "releases" else v for k, v in world.items()}
    db = tmp_path / "events.db"
    load_seed(world, path=db)
    monkeypatch.setattr(main, "DB", db)
    monkeypatch.setattr(main, "UPLOADS", tmp_path / "uploads")
    client = TestClient(main.app)

    before = pipeline.decide(events.state(path=db), rid)
    assert any(r.rule == "04_conditional_release_present" and not r.passed for r in before.results)

    vendor = next(v for v in world["vendors"] if v.id == release.sub_id)
    job = next(j for j in world["jobs"] if j.id == release.job_id)
    pdf = tmp_path / "release.pdf"
    render.release_pdf(release, vendor, job, pdf)
    r = _post(client, "/releases", pdf, {"sub_id": release.sub_id, "job_id": release.job_id})
    assert r.status_code == 200, r.text
    got = r.json()["release"]
    assert (got["kind"], got["through_date"], got["signed"]) == (release.kind.value, str(release.through_date), True)

    after = pipeline.decide(events.state(path=db), rid)
    assert next(r for r in after.results if r.rule == "04_conditional_release_present").passed


def test_coi_upload(client, world, tmp_path):
    coi = world["cois"][0]
    vendor = next(v for v in world["vendors"] if v.id == coi.sub_id)
    pdf = tmp_path / "coi.pdf"
    render.coi_pdf(coi, vendor, pdf)
    r = _post(client, "/cois", pdf, {"sub_id": coi.sub_id})
    assert r.status_code == 200, r.text
    stored = next(c for c in events.state(path=main.DB)["cois"] if c.id == r.json()["coi_id"])
    assert (stored.gl_expiry, stored.wc_expiry, stored.additional_insured) == (coi.gl_expiry, coi.wc_expiry,
                                                                               coi.additional_insured)
    assert _post(client, "/cois", pdf, {"sub_id": coi.sub_id}).status_code == 409
    assert _post(client, "/cois", pdf, {"sub_id": "sub_nobody"}).status_code == 404


def test_email_upload_keeps_the_lookalike_domain(client, tmp_path, monkeypatch):
    from perception import llm
    monkeypatch.setattr(llm, "classify_email", lambda s, b, client=None: llm.EmailFlags(
        asks_to_change_payment_details=False, pressures_for_speed=False))
    from datetime import datetime
    from shared.schema import Email, EmailClaims
    e = Email(id="x", from_addr="accounting@brightIineelectric.com", from_domain="brightIineelectric.com",
              sent_at=datetime(2026, 10, 22, 9, 14), subject="Updated remittance details",
              body="We moved banks. Send payments to account 447712782:5071050724 immediately.",
              claims=EmailClaims())
    eml_path = tmp_path / "m.eml"
    render.email_eml(e, eml_path)
    r = _post(client, "/emails", eml_path, {}, name="m.eml", mime="message/rfc822")
    assert r.status_code == 200, r.text
    got = r.json()["email"]
    assert got["from_domain"] == "brightIineelectric.com" and got["claims"] == {
        "bank_change": True, "new_account": "447712782:5071050724", "urgency": True}


def test_pay_app_with_separate_g702(client, world, tmp_path):
    from tests.perception.g703_builder import build_aia_g702, build_g703, standard_lines
    req = next(r for r in world["pay_requests"] if r.id == "pr_sub_02_6")
    g703 = build_g703(tmp_path / "g703.pdf", standard_lines(), g702=False)
    g702 = build_aia_g702(tmp_path / "g702.pdf", standard_lines())
    with open(g703, "rb") as a, open(g702, "rb") as b:
        r = client.post("/pay-apps", files={"file": ("g703.pdf", a, "application/pdf"),
                                            "g702": ("g702.pdf", b, "application/pdf")},
                        data={"sub_id": req.sub_id, "job_id": req.job_id})
    assert r.status_code == 200, r.text
    assert r.json()["pay_request"]["g702"]["current_payment_due"] == "59850.00"


# --- no Anthropic key: every Claude-backed upload fails plainly, never a 500 -----


@pytest.fixture
def no_credentials(monkeypatch):
    """What the SDK does when no API key or credential is configured: a TypeError at request time."""
    from types import SimpleNamespace
    from perception import llm

    def parse(**kw):
        raise TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token, or '
                        'credentials to be set. Or for the `Authorization` header to be explicitly omitted"')

    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse))))


def test_scanned_pay_app_without_a_key_is_a_plain_422(client, world, tmp_path, no_credentials):
    from tests.perception.g703_builder import rasterize
    req, path = rendered_pay_app(world, tmp_path)
    scan = rasterize(path, tmp_path / "scan.pdf")
    r = upload(client, scan, req.sub_id, req.job_id)
    assert r.status_code == 422 and "no Anthropic credentials" in r.json()["detail"]


def test_video_without_a_key_is_a_plain_422(client, world, tmp_path, no_credentials):
    video = make_video(tmp_path / "walk.mp4", seconds=2)
    with open(video, "rb") as f:
        r = client.post("/site-video", files={"file": ("walk.mp4", f, "video/mp4")}, data={"job_id": world["jobs"][0].id})
    assert r.status_code == 422 and "no Anthropic credentials" in r.json()["detail"]


def test_email_without_a_key_still_uploads_with_code_claims(client, tmp_path, no_credentials):
    from datetime import datetime
    from shared.schema import Email, EmailClaims
    e = Email(id="x", from_addr="accounting@brightIineelectric.com", from_domain="brightIineelectric.com",
              sent_at=datetime(2026, 10, 22, 9, 14), subject="Updated remittance details",
              body="Send payments to account 447712782:5071050724 immediately.", claims=EmailClaims())
    path = tmp_path / "m.eml"
    render.email_eml(e, path)
    r = _post(client, "/emails", path, {}, name="m.eml", mime="message/rfc822")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "parsed" and body["email"]["claims"]["bank_change"]
    assert "model_unavailable" in {n["code"] for n in body["notes"]}


def test_needs_ok_sentence_has_one_period():
    from decimal import Decimal
    from shared.schema import Action, Decision
    d = Decision(request_id="r", action=Action.escalate, results=[])
    assert main.sentence(d, "Airflow HVAC Inc.", Decimal("63723.60")) == "Needs your OK: $63,723.60 to Airflow HVAC Inc."


class FakeDownload:
    """Stands in for vercel.blob.sync.stream_blob: yields the stored bytes in chunks."""

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for i in range(0, len(self.data), 64 * 1024):
            yield self.data[i:i + 64 * 1024]


def test_ingest_reads_a_client_upload_from_private_blob(client, world, tmp_path, monkeypatch):
    from vercel.blob import sync as blob
    req, path = rendered_pay_app(world, tmp_path)
    store = {"uploads/payapp-x7Q2.pdf": path.read_bytes()}
    asked, archived = [], []

    def stream_blob(pathname, access):
        asked.append((pathname, access))
        if pathname not in store:
            raise blob.BlobNotFoundError("missing")
        return FakeDownload(store[pathname])

    monkeypatch.setattr(blob, "stream_blob", stream_blob)
    monkeypatch.setattr(main, "_archive", lambda data, name: archived.append(name))
    r = client.post("/ingest", json={"kind": "pay_app", "pathname": "uploads/payapp-x7Q2.pdf",
                                     "sub_id": req.sub_id, "job_id": req.job_id})
    assert r.status_code == 200, r.text
    stored = next(p for p in events.state(path=main.DB)["pay_requests"] if p.id == r.json()["request_id"])
    assert stored.lines == req.lines and stored.g702 == req.g702
    assert asked == [("uploads/payapp-x7Q2.pdf", "private")]
    assert len(archived) == 1 and main.HASHED.fullmatch(archived[0])  # kept by hash too, so GET /files finds it
    assert not any(p.name.startswith("incoming-") for p in (tmp_path / "uploads").iterdir())  # temp file cleaned up

    assert client.post("/ingest", json={"kind": "pay_app", "pathname": "uploads/nope.pdf",
                                        "sub_id": req.sub_id, "job_id": req.job_id}).status_code == 404
    assert client.post("/ingest", json={"kind": "pay_app", "pathname": "../secrets.pdf",
                                        "sub_id": req.sub_id, "job_id": req.job_id}).status_code == 422
    assert client.post("/ingest", json={"kind": "pay_app", "pathname": "uploads/payapp-x7Q2.pdf"}).status_code == 422
    assert client.post("/ingest", json={"kind": "tax_return", "pathname": "uploads/a.pdf"}).status_code == 422


def _email_with(tmp_path, from_addr, attachments):
    """An .eml like a sub would send: a short body plus PDF attachments."""
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = from_addr, "ap@ridgelinebuilders.com", "October pay app"
    msg["Date"] = "Sun, 25 Oct 2026 09:14:00 -0700"
    msg.set_content("Hi, attached is our October pay application with the conditional release and our COI.")
    for path in attachments:
        msg.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
    out = tmp_path / "mail.eml"
    out.write_bytes(bytes(msg))
    return out


def _docs(world, tmp_path, sub_id="sub_02"):
    from reportlab.pdfgen import canvas
    req, g703 = rendered_pay_app(world, tmp_path, f"pr_{sub_id}_6")
    vendor = next(v for v in world["vendors"] if v.id == sub_id)
    job = next(j for j in world["jobs"] if j.id == req.job_id)
    rel = next(r for r in world["releases"] if r.id == f"rel_c_{req.id}")
    coi = next(c for c in world["cois"] if c.sub_id == sub_id)
    render.release_pdf(rel, vendor, job, tmp_path / "release.pdf")
    render.coi_pdf(coi, vendor, tmp_path / "coi.pdf")
    c = canvas.Canvas(str(tmp_path / "invoice.pdf")); c.drawString(72, 720, "INVOICE #4471 for lunch"); c.save()
    return vendor, [g703, tmp_path / "release.pdf", tmp_path / "coi.pdf", tmp_path / "invoice.pdf"]


def post_email(client, path):
    with open(path, "rb") as f:
        return client.post("/emails", files={"file": ("mail.eml", f, "message/rfc822")})


def test_email_attachments_are_filed_under_the_exact_sender(client, world, tmp_path):
    vendor, docs = _docs(world, tmp_path)
    r = post_email(client, _email_with(tmp_path, f"Accounting <ap@{vendor.email_domain}>", docs))
    assert r.status_code == 200, r.text
    rows = {a["filename"]: a for a in r.json()["attachments"]}
    assert [rows[n]["kind"] for n in ("g703.pdf", "release.pdf", "coi.pdf", "invoice.pdf")] == ["pay_app", "release", "coi", None]
    assert {n: rows[n]["status"] for n in rows} == {"g703.pdf": "recorded", "release.pdf": "recorded",
                                                     "coi.pdf": "recorded", "invoice.pdf": "needs_person"}
    state = events.state(path=main.DB)
    pr_id = rows["g703.pdf"]["result"]["request_id"]
    assert any(p.id == pr_id and p.sub_id == vendor.id for p in state["pay_requests"])


def test_lookalike_sender_files_nothing(client, world, tmp_path):
    from seed.anomalies import confusable
    vendor, docs = _docs(world, tmp_path)
    fake = confusable(vendor.email_domain)
    r = post_email(client, _email_with(tmp_path, f"ap@{fake}", docs[:1]))
    [row] = r.json()["attachments"]
    assert row["kind"] == "pay_app" and row["status"] == "needs_person" and fake in row["detail"]
    assert len(events.state(path=main.DB)["pay_requests"]) == len(world["pay_requests"])  # nothing filed


def test_email_without_attachments_still_works(client, world, tmp_path):
    vendor, _ = _docs(world, tmp_path)
    r = post_email(client, _email_with(tmp_path, f"ap@{vendor.email_domain}", []))
    assert r.status_code == 200 and r.json()["attachments"] == []


def _postmark(client, json, password="s3cret"):
    import base64
    auth = base64.b64encode(f"postmark:{password}".encode()).decode()
    return client.post("/inbound/postmark", json=json, headers={"Authorization": f"Basic {auth}"})


def test_postmark_inbound_files_attachments(client, world, tmp_path, monkeypatch):
    import base64
    monkeypatch.setenv("INBOUND_PASSWORD", "s3cret")
    vendor, docs = _docs(world, tmp_path)
    payload = {"MessageID": "abc", "From": f"ap@{vendor.email_domain}", "To": "draw@inbound.postmarkapp.com",
               "Subject": "October pay app", "Date": "Sun, 25 Oct 2026 09:14:00 -0700", "TextBody": "Attached.",
               "Attachments": [{"Name": p.name, "ContentType": "application/pdf",
                                "Content": base64.b64encode(p.read_bytes()).decode()} for p in docs[:2]]}
    r = _postmark(client, payload)
    assert r.status_code == 200, r.text
    assert [a["status"] for a in r.json()["attachments"]] == ["recorded", "recorded"]
    retry = _postmark(client, payload)  # Postmark retries: 200, nothing filed twice
    assert retry.status_code == 200 and {a["status"] for a in retry.json()["attachments"]} == {"duplicate"}
    raw = _email_with(tmp_path, f"ap@{vendor.email_domain}", docs[2:3]).read_text()
    assert _postmark(client, {"RawEmail": raw}).json()["attachments"][0]["status"] == "recorded"


def test_postmark_inbound_needs_the_password(client, monkeypatch):
    assert _postmark(client, {}).status_code == 503  # off with no password set
    monkeypatch.setenv("INBOUND_PASSWORD", "s3cret")
    assert _postmark(client, {}, password="guess").status_code == 403
    assert client.post("/inbound/postmark", json={}).status_code == 403
