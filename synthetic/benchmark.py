"""Score perception's G703 extractor against the synthetic documents' truth.json. Owner: B.

    python -m synthetic.benchmark [dir] [--llm]     # default data/synthetic, text layer only

Every field the extractor returns is compared exactly with the record the PDF was rendered
from. Without --llm, Claude is never called (free, deterministic); with it, documents the
text layer can't read fall back to Claude, as in production.
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from perception.g703 import extract_pay_request

LINE_FIELDS = ["item_no", "description", "scheduled_value", "previous_applications", "this_period",
               "stored_materials", "total_to_date", "pct_complete", "balance_to_finish", "retainage"]
G702_FIELDS = ["original_contract_sum", "net_change_orders", "contract_sum_to_date", "total_completed_stored",
               "retainage", "total_earned_less_retainage", "less_previous_certificates", "current_payment_due"]
TOP_FIELDS = ["number", "period_end", "bank_account"]


def same(a, b) -> bool:
    """Numbers compare by value (48 == 48.00); everything else as text."""
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except Exception:
        return str(a) == str(b)


def compare(got: dict, want: dict) -> dict[str, bool]:
    out = {f: same(got.get(f), want[f]) for f in TOP_FIELDS}
    out |= {f"g702.{f}": same((got.get("g702") or {}).get(f), want["g702"][f]) for f in G702_FIELDS}
    got_lines = {l["item_no"]: l for l in got.get("lines", [])}
    for wl in want["lines"]:
        gl = got_lines.get(wl["item_no"], {})
        out |= {f"line.{f}#{wl['item_no']}": same(gl.get(f), wl[f]) for f in LINE_FIELDS}
    out["line_count"] = len(got.get("lines", [])) == len(want["lines"])
    return out


def run(root: Path, llm: bool = False) -> dict:
    folders = sorted(p.parent for p in root.rglob("truth.json"))
    sets = {"total": len(folders), "extracted": 0, "all_fields_exact": 0}
    errors, methods = Counter(), Counter()
    field_ok, field_n = Counter(), Counter()
    by_style = defaultdict(lambda: {"sets": 0, "exact": 0})
    failures = []
    for f in folders:
        truth = json.loads((f / "truth.json").read_text())
        want, style = truth["pay_request"], truth["style"]
        by_style[style]["sets"] += 1
        try:
            res = extract_pay_request(f / "g703.pdf", sub_id=want["sub_id"], job_id=want["job_id"],
                                      submitted_at=datetime.fromisoformat(want["submitted_at"]),
                                      request_id=want["id"], allow_llm_fallback=llm)
        except Exception as e:  # a failure to read is a result, not a crash
            errors[type(e).__name__] += 1
            failures.append({"folder": str(f), "error": f"{type(e).__name__}: {str(e)[:200]}"})
            continue
        sets["extracted"] += 1
        methods[res.method] += 1
        result = compare(res.pay_request.model_dump(mode="json"), want)
        for key, ok in result.items():
            name = key.split("#")[0]
            field_n[name] += 1
            field_ok[name] += ok
        if all(result.values()):
            sets["all_fields_exact"] += 1
            by_style[style]["exact"] += 1
        else:
            failures.append({"folder": str(f), "wrong": [k for k, ok in result.items() if not ok][:10]})
    return {
        "sets": sets, "methods": dict(methods), "errors": dict(errors),
        "field_accuracy": {k: round(field_ok[k] / field_n[k], 4) for k in sorted(field_n)},
        "by_style": dict(by_style), "failures": failures[:50],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="data/synthetic", type=Path)
    ap.add_argument("--llm", action="store_true", help="allow the Claude fallback (costs money)")
    args = ap.parse_args()
    report = run(args.root, args.llm)
    out = Path("data/perception_benchmark.json")
    out.write_text(json.dumps(report, indent=2))
    s = report["sets"]
    print(f"{s['extracted']}/{s['total']} extracted, {s['all_fields_exact']} with every field exact -> {out}")
    print(f"methods {report['methods']} | errors {report['errors']}")
    worst = sorted(report["field_accuracy"].items(), key=lambda kv: kv[1])[:6]
    print("lowest field accuracy: " + ", ".join(f"{k} {v:.1%}" for k, v in worst))
    print("by style: " + ", ".join(f"{k} {v['exact']}/{v['sets']}" for k, v in report["by_style"].items()))
