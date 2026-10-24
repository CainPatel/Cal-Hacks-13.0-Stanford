"""python -m synthetic [out_dir]     (default data/synthetic)

Writes one folder per pay request, each holding what the GC would receive for it plus
truth.json, the exact records the documents were rendered from:

    oct/<request_id>/       this month's 15 requests (incl. the 3 demo holds)
    cases/<NN>_<anomaly>/   the 25 planted anomalies (truth.json also has the Label)

Each folder: g703.pdf, g703.xlsx, release.pdf (if the sub sent one), coi.pdf,
email_*.eml (if any). The PDF's number style is in truth.json["style"].
"""
import json
import random
import sys
from pathlib import Path

from seed.anomalies import THIS_MONTH, cases, with_demo
from seed.generate import generate
from shared.schema import ReleaseKind
from synthetic import render


def render_request(data, request_id, folder: Path, label=None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    req = next(r for r in data["pay_requests"] if r.id == request_id)
    vendor = next(v for v in data["vendors"] if v.id == req.sub_id)
    job = next(j for j in data["jobs"] if j.id == req.job_id)
    coi = next(c for c in data["cois"] if c.sub_id == req.sub_id)
    # the conditional release sent with this request, if any (a stale one carries an older through date)
    release = next((r for r in data["releases"] if r.id == f"rel_c_{req.id}"
                    and r.kind == ReleaseKind.conditional_progress), None)
    emails = [e for e in data["emails"] if e.claims.new_account == req.bank_account or e.from_domain == vendor.email_domain]
    style = random.Random(request_id + folder.name).choice(sorted(render.STYLES))

    render.g703_pdf(req, vendor, job, folder / "g703.pdf", style)
    render.g703_xlsx(req, vendor, job, folder / "g703.xlsx")
    render.coi_pdf(coi, vendor, folder / "coi.pdf")
    if release:
        render.release_pdf(release, vendor, job, folder / "release.pdf")
    for e in emails:
        render.email_eml(e, folder / f"email_{e.id}.eml")
    truth = {
        "style": style,
        "pay_request": req.model_dump(mode="json"),
        "vendor": vendor.model_dump(mode="json"),
        "release": release.model_dump(mode="json") if release else None,
        "coi": coi.model_dump(mode="json"),
        "emails": [e.model_dump(mode="json") for e in emails],
        "label": label.model_dump(mode="json") if label else None,
    }
    (folder / "truth.json").write_text(json.dumps(truth, indent=2))


def main(out: Path) -> int:
    data, _ = with_demo(generate())
    n = 0
    for req in data["pay_requests"]:
        if req.number == THIS_MONTH:
            render_request(data, req.id, out / "oct" / req.id)
            n += 1
    for i, (case_data, label) in enumerate(cases()):
        render_request(case_data, label.request_id, out / "cases" / f"{i:02d}_{label.anomaly}", label)
        n += 1
    return n


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/synthetic")
    print(f"rendered {main(out)} document sets into {out}")
