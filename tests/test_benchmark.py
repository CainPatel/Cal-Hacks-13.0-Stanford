"""The perception benchmark counts every field and notices when one is wrong."""
import json

from synthetic.__main__ import render_request
from synthetic.benchmark import compare, run
from seed.anomalies import with_demo
from seed.generate import generate


def test_benchmark_on_demo_requests(tmp_path):
    data, _ = with_demo(generate())
    for rid in ["pr_sub_03_6", "pr_sub_10_6", "pr_sub_02_1"]:
        render_request(data, rid, tmp_path / rid)
    report = run(tmp_path)
    assert report["sets"] == {"total": 3, "extracted": 3, "all_fields_exact": 3}
    assert report["methods"] == {"text_layer": 3}


def test_compare_flags_wrong_values(tmp_path):
    data, _ = with_demo(generate())
    render_request(data, "pr_sub_03_6", tmp_path / "a")
    want = json.loads((tmp_path / "a" / "truth.json").read_text())["pay_request"]
    got = json.loads(json.dumps(want))
    got["g702"]["current_payment_due"] = "1.00"
    got["lines"][1]["pct_complete"] = "45"
    wrong = [k for k, ok in compare(got, want).items() if not ok]
    assert wrong == ["g702.current_payment_due", "line.pct_complete#09-20"]
