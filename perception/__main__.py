"""Run perception on a file and print the result as JSON.

    python -m perception g703 path/to/payapp.pdf [--no-fallback]
    python -m perception video path/to/walkthrough.mp4 --job job_elm --sov path/to/payapp.pdf
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone

from perception import ExtractionError, VideoError, build_site_evidence, extract_pay_request


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m perception")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("g703")
    g.add_argument("pdf")
    g.add_argument("--no-fallback", action="store_true", help="never call Claude")
    v = sub.add_parser("video")
    v.add_argument("video")
    v.add_argument("--job", required=True)
    v.add_argument("--sov", required=True, help="a G703 PDF whose line items name the work to look for")
    v.add_argument("--frames-dir", default="data/frames")
    v.add_argument("--n-frames", type=int, default=10)
    args = ap.parse_args()

    try:
        if args.cmd == "g703":
            r = extract_pay_request(args.pdf, sub_id="cli", job_id="cli", submitted_at=datetime.now(timezone.utc),
                                    allow_llm_fallback=not args.no_fallback)
            out = {"method": r.method, "fallback_reason": r.fallback_reason, "sha256": r.sha256,
                   "notes": [asdict(n) for n in r.notes], "pay_request": r.pay_request.model_dump(mode="json")}
        else:
            sov = extract_pay_request(args.sov, sub_id="cli", job_id=args.job, submitted_at=datetime.now(timezone.utc))
            r = build_site_evidence(args.video, job_id=args.job, upload_date=date.today(),
                                    line_items=sov.pay_request.lines, frames_dir=args.frames_dir, n_frames=args.n_frames)
            out = {"date_source": r.date_source, "notes": [asdict(n) for n in r.notes],
                   "evidence": r.evidence.model_dump(mode="json"),
                   "raw": r.raw.model_dump(mode="json") if r.raw else None}
    except (ExtractionError, VideoError) as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
