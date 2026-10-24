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

    def fake_replay(through, path, judge=None):
        calls.update(through=through, judge=judge)
        return []

    monkeypatch.setattr(main, "replay", fake_replay)
    client.post("/replay", params={"through": 6})
    assert calls["judge"] is None
    client.post("/replay", params={"through": 6, "judge": "true"})
    from judgment import judge
    assert calls["judge"] is judge
