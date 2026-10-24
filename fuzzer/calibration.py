"""Is Jev's confidence worth anything, and where should the escalation threshold sit? Owner: B.

    python -m fuzzer.calibration 400      # judge 400 fuzz cases (calls Jev / Claude), write data/calibration.json

Every Jev answer on a labeled case is right or wrong (see `outcomes`). From those:
- the calibration curve: stated confidence vs. how often it was actually right, binned;
- the threshold: the lowest confidence cut at which we can claim, with 95% confidence, that
  answers at or above it are wrong at most 1% of the time. Each of the 50 candidate cuts
  (0.50-0.99) gets a Clopper-Pearson upper bound on its error rate at level 5%/50
  (Bonferroni), so the claim holds for whichever cut we end up picking. It is chosen on one
  half of the data and checked on the other half.
Rule 15 escalates any request with an answer below the threshold, so this bounds how often a
wrong-but-confident answer gets past the floor.
"""
import json
import math
import random
import sys
from pathlib import Path

from shared.schema import Label

RELEASE, BANK, PROGRESS, DUPLICATE, ACTION = (
    "release_form_correct", "bank_change_suspicious", "progress_plausible", "duplicate_likelihood", "proposed_action")


def outcomes(answers: dict, label: Label) -> list[tuple[str, float, bool]]:
    """(question, confidence, was it right) for each answer, judged against the case's label."""
    an = label.anomaly or ""
    truth = {
        RELEASE: lambda v: v == ("incomplete" if "missing_conditional_release" in an else "correct"),
        BANK: lambda v: (v >= 0.5) == ("bank_change" in an),
        PROGRESS: lambda v: v == ("overstated" if any(k in an for k in ("overbill_pct", "past_sov", "inflated_scheduled_value"))
                                  else "plausible"),
        DUPLICATE: lambda v: (v >= 0.5) == ("duplicate" in an),
        # the errors that cost money: releasing an attack, or holding an honest request.
        # Escalating a big clean payment to the button is correct, not a miss.
        ACTION: lambda v: v != "release" if an else v != "hold",
    }
    return [(q, a.confidence, truth[q](a.value)) for q, a in answers.items() if q in truth]


def curve(points, bins: int = 10) -> dict:
    """Binned stated confidence vs. observed accuracy, and the expected calibration error."""
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        inside = [(c, ok) for _, c, ok in points if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if inside:
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(inside),
                         "mean_confidence": round(sum(c for c, _ in inside) / len(inside), 4),
                         "accuracy": round(sum(ok for _, ok in inside) / len(inside), 4)})
    n = sum(r["n"] for r in rows)
    ece = sum(r["n"] / n * abs(r["mean_confidence"] - r["accuracy"]) for r in rows) if n else None
    return {"bins": rows, "expected_calibration_error": round(ece, 4) if ece is not None else None}


def error_upper_bound(errors: int, n: int, delta: float) -> float:
    """Clopper-Pearson: the largest error rate still consistent (at level delta) with seeing `errors` in n."""
    def cdf(p):  # P(X <= errors) for X ~ Binomial(n, p), in log space so large n doesn't underflow
        if p >= 1:
            return 0.0
        return sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                            + (i * math.log(p) if i else 0) + (n - i) * math.log1p(-p)) for i in range(errors + 1))
    lo, hi = errors / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > delta else (lo, mid)
    return hi


def threshold(points, alpha: float = 0.01, delta: float = 0.05) -> dict:
    """Lowest cut whose accepted answers provably (1 - delta) err at most alpha; None if no cut does."""
    cuts = [x / 100 for x in range(50, 100)]
    chosen = None
    for t in reversed(cuts):  # keep the lowest passing cut; Bonferroni makes the order irrelevant
        accepted = [ok for _, c, ok in points if c >= t]
        if not accepted:
            continue
        errors = len(accepted) - sum(accepted)
        bound = error_upper_bound(errors, len(accepted), delta / len(cuts))
        if bound > alpha:
            continue
        chosen = {"threshold": t, "accepted": len(accepted), "errors": errors, "error_bound": round(bound, 5),
                  "coverage": round(len(accepted) / len(points), 4)}
    return {"alpha": alpha, "delta": delta, "chosen": chosen}


def holdout_check(points, t: float) -> dict:
    accepted = [ok for _, c, ok in points if c >= t]
    return {"accepted": len(accepted), "coverage": round(len(accepted) / len(points), 4) if points else None,
            "error_rate": round(1 - sum(accepted) / len(accepted), 5) if accepted else None}


def report(points, alpha=0.01, delta=0.05, seed=0) -> dict:
    """Curve on everything; threshold picked on a random half, checked on the other half."""
    shuffled = points[:]
    random.Random(seed).shuffle(shuffled)
    calib, test = shuffled[: len(points) // 2], shuffled[len(points) // 2:]
    picked = threshold(calib, alpha, delta)
    questions = sorted({p[0] for p in points})
    by_q = {q: curve([p for p in points if p[0] == q]) for q in questions}
    per_q = {}
    for q in questions:  # rule 15 applies one floor; this shows what each question would earn alone
        qc, qt = [p for p in calib if p[0] == q], [p for p in test if p[0] == q]
        t = threshold(qc, alpha, delta)
        per_q[q] = {"answers": len(qc) + len(qt), "accuracy": round(sum(p[2] for p in points if p[0] == q) /
                                                                  max(1, sum(1 for p in points if p[0] == q)), 4),
                    "threshold": t["chosen"], "holdout": holdout_check(qt, t["chosen"]["threshold"]) if t["chosen"] else None}
    return {"answers": len(points), "curve": curve(points), "by_question": by_q, "threshold": picked,
            "holdout": holdout_check(test, picked["chosen"]["threshold"]) if picked["chosen"] else None,
            "per_question": per_q}


def collect(n: int, judge=None, rng_seed: int = 0) -> list[tuple[str, float, bool]]:
    """Judge n fuzz cases for real. Needs kernel/ (for KernelState) and a Jev or Claude key."""
    import pipeline
    from fuzzer.run import make_cases
    from judgment.judge import judge as real_judge
    judge = judge or (lambda ks, req: real_judge(ks, req, use_fallback=False))  # Jev only: don't mix in Claude
    contract = pipeline.default_contract()
    points, failed = [], 0
    for data, label in make_cases(n, random.Random(rng_seed)):
        ks, req = pipeline.kernel_state(data, label.request_id, contract)
        result = judge(ks, req)
        if result.source == "unavailable":
            failed += 1
            continue
        points += outcomes(result.answers, label)
    if failed:
        print(f"{failed} of {n} cases got no answer from Jev and were skipped")
    return points


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    points = collect(n)
    Path("data").mkdir(exist_ok=True)
    Path("data/calibration_points.json").write_text(json.dumps(points))  # raw (question, confidence, right)
    out = report(points)
    Path("data/calibration.json").write_text(json.dumps(out, indent=2))
    c = out["threshold"]["chosen"]
    print(f"{out['answers']} answers | ECE {out['curve']['expected_calibration_error']} | "
          + (f"threshold {c['threshold']} (coverage {c['coverage']:.0%}), holdout error {out['holdout']['error_rate']}"
             if c else "no threshold meets the bound: escalate everything below 0.99"))
