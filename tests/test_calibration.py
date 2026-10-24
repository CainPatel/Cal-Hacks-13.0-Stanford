"""The calibration math: curve, Clopper-Pearson bound, threshold search, label-based correctness."""
import random
from dataclasses import dataclass

from fuzzer import calibration as cal
from shared.schema import Action, Label


def test_upper_bound_matches_known_values():
    assert abs(cal.error_upper_bound(0, 299, 0.05) - (1 - 0.05 ** (1 / 299))) < 1e-6  # zero errors: closed form
    assert 0.0099 < cal.error_upper_bound(0, 299, 0.05) < 0.0101  # ~300 clean answers to claim 1%
    assert cal.error_upper_bound(5, 100, 0.05) > 0.05  # can't claim better than observed


def test_curve_of_a_well_calibrated_model():
    rng = random.Random(0)
    pts = []
    for _ in range(20000):
        c = rng.random()
        pts.append(("q", c, rng.random() < c))  # right exactly as often as it claims
    out = cal.curve(pts)
    assert out["expected_calibration_error"] < 0.02 and len(out["bins"]) == 10


def test_threshold_finds_the_safe_cut():
    rng = random.Random(1)
    # confident answers (>=0.9) are always right; lower ones are a coin flip
    pts = [("q", c, True if c >= 0.9 else rng.random() < 0.5) for c in (rng.uniform(0.5, 1) for _ in range(6000))]
    t = cal.threshold(pts)["chosen"]
    assert 0.88 <= t["threshold"] <= 0.91 and t["errors"] <= 1 and t["error_bound"] <= 0.01


def test_overconfident_model_gets_no_threshold():
    pts = [("q", 0.99, i % 10 != 0) for i in range(1000)]  # says 99%, right 90%
    assert cal.threshold(pts)["chosen"] is None


@dataclass
class A:
    value: object
    confidence: float


def test_outcomes_use_the_label():
    bad = Label(request_id="x", anomaly="bank_change_homoglyph", expected_action=Action.hold, rules=["rule_10"])
    clean = Label(request_id="y", anomaly=None, expected_action=Action.release, rules=[])
    answers = {cal.BANK: A(0.9, 0.95), cal.ACTION: A("release", 0.8), cal.RELEASE: A("correct", 0.9)}
    assert cal.outcomes(answers, bad) == [(cal.BANK, 0.95, True), (cal.ACTION, 0.8, False), (cal.RELEASE, 0.9, True)]
    assert [ok for _, _, ok in cal.outcomes(answers, clean)] == [False, True, True]


def test_report_splits_and_checks_holdout():
    rng = random.Random(2)
    pts = [("q", c, True if c >= 0.9 else rng.random() < 0.6) for c in (rng.uniform(0.5, 1) for _ in range(8000))]
    out = cal.report(pts)
    assert out["threshold"]["chosen"] and out["holdout"]["error_rate"] <= 0.01


def test_progress_is_judged_against_the_measured_gaps_when_facts_are_given():
    from types import SimpleNamespace
    from shared.schema import Action, Label
    resubmitted = Label(request_id="r", anomaly="duplicate_new_number", expected_action=Action.hold, rules=["rule_12"])
    answers = {cal.PROGRESS: SimpleNamespace(value="understated", confidence=0.95)}
    below = {"progress": {"lines_billed_above_video_beyond_tolerance": [],
                          "lines_billed_below_video_beyond_tolerance": ["09-10"]}}
    assert cal.outcomes(answers, resubmitted) == [(cal.PROGRESS, 0.95, False)]       # by the label's name
    assert cal.outcomes(answers, resubmitted, below) == [(cal.PROGRESS, 0.95, True)]  # by what the video shows
